from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from obs_self_heal.config import AppConfig
from obs_self_heal.cooldowns import CooldownStore
from obs_self_heal.models import (
    IncidentClass,
    IncidentContext,
    ObsStreamState,
    PublicStreamHealth,
    ReachabilityResult,
    RemediationAction,
    ScriptRunResult,
)
from obs_self_heal.wrappers import obs as obs_wrapper
from obs_self_heal.wrappers import obs_control_api as obs_control_api_wrapper
from obs_self_heal.wrappers import scripts as scripts_wrapper
from obs_self_heal.wrappers import unraid as unraid_wrapper


@dataclass
class ClassificationResult:
    incident_class: IncidentClass
    evidence: dict[str, Any]


@dataclass
class RemediationPlan:
    action: RemediationAction
    reason: str
    cooldown_key: str


def classify_incident(
    cfg: AppConfig,
    public: PublicStreamHealth,
    ws_reachable: bool,
    stream: ObsStreamState,
    obs_vm: ReachabilityResult | None,
    unraid: ReachabilityResult | None,
) -> ClassificationResult:
    """Map signals to `IncidentClass` (MVP rules)."""

    evidence: dict[str, Any] = {
        "public_exit_code": public.exit_code,
        "public_parse_error": public.parse_error,
        "public_evaluation_delegated": public.public_evaluation_delegated,
        "critical_count": public.critical_count,
        "down_count": public.down_count,
        "ws_reachable": ws_reachable,
        "stream_active": stream.output_active,
        "stream_error": stream.error,
        "obs_vm": _reach_summary(obs_vm),
        "unraid": _reach_summary(unraid),
    }

    public_ok = public.is_public_healthy(cfg.thruk.critical_threshold, cfg.thruk.down_unhealthy)
    degraded = public.is_degraded(cfg.thruk.warning_only_is_degraded)
    overall_public_bad = (not public_ok) or degraded

    vm_ok = _vm_network_ok(obs_vm)
    ws_ok = ws_reachable and stream.error is None

    if not overall_public_bad:
        if ws_ok and stream.output_active is not False:
            if cfg.obs.expected_streaming_when_healthy and stream.output_active is False:
                return ClassificationResult(
                    IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_INACTIVE,
                    {**evidence, "note": "public_ok_but_stream_inactive_while_expected"},
                )
            return ClassificationResult(IncidentClass.HEALTHY, evidence)
        if not ws_reachable and vm_ok:
            return ClassificationResult(IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE, evidence)
        if not vm_ok:
            return ClassificationResult(IncidentClass.VM_OR_NETWORK_UNHEALTHY, evidence)
        return ClassificationResult(IncidentClass.UNKNOWN, evidence)

    # Public not OK or degraded path
    if degraded and ws_ok and vm_ok and stream.output_active is True:
        return ClassificationResult(IncidentClass.DEGRADED_SUSPECTED_CAPTURE, evidence)

    if not vm_ok:
        return ClassificationResult(IncidentClass.VM_OR_NETWORK_UNHEALTHY, evidence)

    if not ws_reachable:
        return ClassificationResult(IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE, evidence)

    if stream.output_active is True:
        return ClassificationResult(IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE, evidence)

    if stream.output_active is False:
        return ClassificationResult(IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_INACTIVE, evidence)

    return ClassificationResult(IncidentClass.UNKNOWN, evidence)


def _reach_summary(r: ReachabilityResult | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {"host": r.host, "ping_ok": r.ping_ok, "tcp_ok": r.tcp_ok, "error": r.error}


def _vm_network_ok(obs_vm: ReachabilityResult | None) -> bool:
    if obs_vm is None:
        return True
    if obs_vm.ping_ok is True:
        return True
    if obs_vm.tcp_ok and any(obs_vm.tcp_ok.values()):
        return True
    return False


def choose_remediation(
    cfg: AppConfig,
    incident_class: IncidentClass,
    cooldowns: CooldownStore,
) -> RemediationPlan:
    """Select the next single remediation respecting cooldowns (caller enforces max actions)."""

    if incident_class == IncidentClass.HEALTHY:
        return RemediationPlan(RemediationAction.NONE, "healthy", "none")

    cd = cfg.policy.cooldown_sec

    if incident_class == IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_INACTIVE:
        key = "obs_start_stream"
        cool = float(cd.obs_start_stream)
        action = (
            RemediationAction.RUN_START_STREAM_SCRIPT
            if cfg.policy.prefer_script_for_stream_toggle
            else RemediationAction.OBS_START_STREAM_WEBSOCKET
        )
        if cooldowns.allowed(key, cool):
            return RemediationPlan(action, "public_down_and_obs_not_streaming", key)
        return RemediationPlan(RemediationAction.RECHECK_ONLY, "cooldown_obs_start_stream", "recheck")

    if incident_class == IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE:
        # Public monitoring can lag substantially after OBS reports streaming active.
        grace_key = "public_recover_grace"
        if not cooldowns.allowed(grace_key, float(cd.public_recover_grace)):
            return RemediationPlan(RemediationAction.RECHECK_ONLY, "public_monitoring_lag_grace", "recheck")

        key2 = "stream_stop_start"
        if cooldowns.allowed(key2, float(cd.stream_stop_start)):
            return RemediationPlan(
                RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS,
                "public_down_but_obs_streaming_waited_grace_try_controlled_restart",
                key2,
            )
        return RemediationPlan(RemediationAction.RECHECK_ONLY, "cooldown_restart_after_grace", "recheck")

    if incident_class == IncidentClass.DEGRADED_SUSPECTED_CAPTURE:
        key = "capture_reset"
        if cooldowns.allowed(key, float(cd.capture_reset)):
            return RemediationPlan(
                RemediationAction.RUN_CAPTURE_DEVICES_RESET,
                "degraded_capture_suspected",
                key,
            )
        key2 = "stream_stop_start"
        if cooldowns.allowed(key2, float(cd.stream_stop_start)):
            return RemediationPlan(
                RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS,
                "capture_reset_on_cooldown_try_controlled_restart",
                key2,
            )
        return RemediationPlan(RemediationAction.RECHECK_ONLY, "cooldown_capture_and_restart", "recheck")

    if incident_class == IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE:
        key_api = "obs_control_api_restart"
        if cfg.obs_control_api is not None:
            # Prefer the Windows-host control API when websocket is down; script-based toggles typically
            # also depend on websocket and can hang/thrash when OBS isn't running.
            if cooldowns.allowed(key_api, float(cd.obs_control_api_restart)):
                return RemediationPlan(
                    RemediationAction.RESTART_OBS_VIA_CONTROL_API,
                    "ws_unreachable_try_windows_side_control_api",
                    key_api,
                )
            return RemediationPlan(RemediationAction.RECHECK_ONLY, "cooldown_obs_control_api_restart", "recheck")
        key = "stream_stop_start"
        if cooldowns.allowed(key, float(cd.stream_stop_start)):
            return RemediationPlan(
                RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS,
                "ws_unreachable_try_script_based_obs_cli",
                key,
            )
        return RemediationPlan(RemediationAction.ESCALATE_OPERATOR, "cooldown_control_api_and_stream_toggle", "escalate")

    if incident_class == IncidentClass.VM_OR_NETWORK_UNHEALTHY:
        key = "vm_restart"
        if not cfg.policy.allow_vm_restart:
            return RemediationPlan(RemediationAction.ESCALATE_OPERATOR, "vm_unhealthy_vm_restart_disabled", "escalate")
        if cooldowns.allowed(key, float(cd.vm_restart)):
            return RemediationPlan(RemediationAction.RESTART_OBS_VM, "vm_or_network_unhealthy", key)
        return RemediationPlan(RemediationAction.ESCALATE_OPERATOR, "vm_unhealthy_vm_restart_on_cooldown", "escalate")

    return RemediationPlan(RemediationAction.ESCALATE_OPERATOR, "unclassified", "escalate")


def execute_remediation(
    cfg: AppConfig,
    ctx: IncidentContext,
    action: RemediationAction,
    cooldowns: CooldownStore,
    dry_run: bool,
) -> ScriptRunResult | None:
    """Execute one remediation action; returns a result object for script-like actions."""

    if action in (RemediationAction.NONE, RemediationAction.RECHECK_ONLY):
        return None

    if dry_run or cfg.maintenance_mode:
        return ScriptRunResult(
            name=f"dry_run_{action.value}",
            exit_code=0,
            stdout="skipped_dry_run_or_maintenance",
            stderr="",
            elapsed_sec=0.0,
            command=[action.value],
        )

    result: ScriptRunResult | None = None

    if action == RemediationAction.OBS_START_STREAM_WEBSOCKET:
        result = obs_wrapper.start_stream_websocket(cfg)
        if result.exit_code == 0:
            cooldowns.touch("obs_start_stream")
            cooldowns.touch("public_recover_grace")
    elif action == RemediationAction.OBS_STOP_STREAM_WEBSOCKET:
        result = obs_wrapper.stop_stream_websocket(cfg)
    elif action == RemediationAction.RESTART_OBS_VIA_CONTROL_API:
        result = obs_control_api_wrapper.restart_obs_via_control_api(cfg)
        cooldowns.touch("obs_control_api_restart")
    elif action == RemediationAction.RUN_CAPTURE_DEVICES_RESET:
        result = scripts_wrapper.run_capture_devices_reset(cfg)
        cooldowns.touch("capture_reset")
    elif action == RemediationAction.RUN_START_STREAM_SCRIPT:
        result = scripts_wrapper.run_start_stream_script(cfg)
        if result.exit_code == 0:
            cooldowns.touch("obs_start_stream")
            cooldowns.touch("public_recover_grace")
    elif action == RemediationAction.RUN_STOP_STREAM_SCRIPT:
        result = scripts_wrapper.run_stop_stream_script(cfg)
    elif action == RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS:
        r1 = scripts_wrapper.run_stop_stream_script(cfg)
        r2 = scripts_wrapper.run_start_stream_script(cfg)
        result = ScriptRunResult(
            name="stop_then_start",
            exit_code=0 if r1.exit_code == 0 and r2.exit_code == 0 else 1,
            stdout=f"stop:{r1.stdout}\nstart:{r2.stdout}",
            stderr=f"stop:{r1.stderr}\nstart:{r2.stderr}",
            elapsed_sec=r1.elapsed_sec + r2.elapsed_sec,
            command=r1.command + r2.command,
        )
        if result.exit_code == 0:
            cooldowns.touch("stream_stop_start")
            cooldowns.touch("public_recover_grace")
    elif action == RemediationAction.RESTART_OBS_VM:
        result = unraid_wrapper.restart_obs_vm(cfg)
        cooldowns.touch("vm_restart")
    elif action == RemediationAction.ESCALATE_OPERATOR:
        return None

    ctx.bump_actions(1 if action != RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS else 2)
    return result


def verify_recovery(
    cfg: AppConfig,
    public_before: PublicStreamHealth,
    public_after: PublicStreamHealth,
) -> dict[str, Any]:
    """Compare public health before/after; extend with OBS checks in orchestrator."""

    before_ok = public_before.is_public_healthy(cfg.thruk.critical_threshold, cfg.thruk.down_unhealthy)
    after_ok = public_after.is_public_healthy(cfg.thruk.critical_threshold, cfg.thruk.down_unhealthy)
    return {
        "public_ok_before": before_ok,
        "public_ok_after": after_ok,
        "improved": (not before_ok) and after_ok,
    }
