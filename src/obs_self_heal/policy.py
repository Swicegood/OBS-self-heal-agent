from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from obs_self_heal.config import AppConfig
from obs_self_heal.cooldowns import CooldownStore
from obs_self_heal.logging_setup import get_logger
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

LOG = get_logger("policy")


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
        "unreachable_count": public.unreachable_count,
        "ws_reachable": ws_reachable,
        "stream_active": stream.output_active,
        "stream_error": stream.error,
        "obs_vm": _reach_summary(obs_vm),
        "unraid": _reach_summary(unraid),
    }

    public_ok = public.is_public_healthy(cfg.thruk.critical_threshold, cfg.thruk.down_unhealthy)
    degraded = public.is_degraded(cfg.thruk.warning_only_is_degraded)
    unreachable_only = public.is_public_unreachable_only(cfg.thruk.critical_threshold)
    overall_public_bad = (not public_ok) or degraded

    vm_ok = _vm_network_ok(obs_vm)
    ws_ok = ws_reachable and stream.error is None

    # Thruk UNREACHABLE (checker cannot reach host) — distinct from DOWN/CRITICAL and from OBS websocket down.
    if unreachable_only and vm_ok:
        if not ws_reachable:
            return ClassificationResult(IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE, evidence)
        if stream.output_active is True:
            return ClassificationResult(IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_ACTIVE, evidence)
        if stream.output_active is False:
            return ClassificationResult(IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_INACTIVE, evidence)
        return ClassificationResult(IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_ACTIVE, evidence)

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


# Sticky progress keys (remember steps already taken across slow probe intervals).
LADDER_CAPTURE_DONE = "ladder_capture_done"
LADDER_STOP_START_DONE = "ladder_stop_start_done"
LADDER_OBS_API_DONE = "ladder_obs_api_done"
LADDER_KEYS = (LADDER_CAPTURE_DONE, LADDER_STOP_START_DONE, LADDER_OBS_API_DONE)


def clear_stream_side_ladder(cooldowns: CooldownStore) -> None:
    """Reset ladder progress when the incident clears (healthy)."""
    cooldowns.clear(*LADDER_KEYS)


def _recently_done(cooldowns: CooldownStore, key: str, sticky_sec: float) -> bool:
    """True if `key` was touched within sticky_sec (progress still counts)."""
    return not cooldowns.allowed(key, sticky_sec)


def _ladder_step_done(cooldowns: CooldownStore, sticky_key: str, action_key: str, sticky_sec: float) -> bool:
    """Sticky progress key, or action key still within the sticky window (upgrade / in-flight)."""
    return _recently_done(cooldowns, sticky_key, sticky_sec) or _recently_done(
        cooldowns, action_key, sticky_sec
    )


def _escalate_after_stream_side_exhausted(
    cfg: AppConfig,
    cooldowns: CooldownStore,
    cd: Any,
    *,
    reason_prefix: str,
) -> RemediationPlan:
    """After capture reset + controlled restart have been tried, escalate to OBS process then VM.

    Uses sticky ladder keys (not short action cooldowns) so a ~5 min probe cadence cannot
    miss the escalate window and loop on capture forever.
    """

    sticky = float(cd.stream_side_ladder)
    api_done = _ladder_step_done(cooldowns, LADDER_OBS_API_DONE, "obs_control_api_restart", sticky)

    key_api = "obs_control_api_restart"
    if (
        cfg.obs_control_api is not None
        and not api_done
        and cooldowns.allowed(key_api, float(cd.obs_control_api_restart))
    ):
        return RemediationPlan(
            RemediationAction.RESTART_OBS_VIA_CONTROL_API,
            f"{reason_prefix}_try_obs_process_restart",
            key_api,
        )

    key_vm = "vm_restart"
    if cfg.policy.allow_vm_restart and cooldowns.allowed(key_vm, float(cd.vm_restart)):
        return RemediationPlan(
            RemediationAction.RESTART_OBS_VM,
            f"{reason_prefix}_try_vm_restart",
            key_vm,
        )

    if cfg.policy.allow_vm_restart:
        return RemediationPlan(
            RemediationAction.ESCALATE_OPERATOR,
            f"{reason_prefix}_vm_restart_on_cooldown",
            "escalate",
        )
    return RemediationPlan(
        RemediationAction.ESCALATE_OPERATOR,
        f"{reason_prefix}_stream_side_exhausted",
        "escalate",
    )


def _choose_stream_side_ladder(
    cfg: AppConfig,
    cooldowns: CooldownStore,
    cd: Any,
    *,
    reason_prefix: str,
    capture_reason: str,
    stop_start_reason: str,
) -> RemediationPlan:
    """Capture → stop/start → OBS API → VM, advancing via sticky progress keys."""

    sticky = float(cd.stream_side_ladder)
    capture_done = _ladder_step_done(cooldowns, LADDER_CAPTURE_DONE, "capture_reset", sticky)
    stop_done = _ladder_step_done(cooldowns, LADDER_STOP_START_DONE, "stream_stop_start", sticky)

    if not capture_done:
        if cooldowns.allowed("capture_reset", float(cd.capture_reset)):
            return RemediationPlan(
                RemediationAction.RUN_CAPTURE_DEVICES_RESET,
                capture_reason,
                "capture_reset",
            )
        return RemediationPlan(RemediationAction.RECHECK_ONLY, "cooldown_capture_reset", "recheck")

    if not stop_done:
        if cooldowns.allowed("stream_stop_start", float(cd.stream_stop_start)):
            return RemediationPlan(
                RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS,
                stop_start_reason,
                "stream_stop_start",
            )
        return RemediationPlan(RemediationAction.RECHECK_ONLY, "cooldown_stream_stop_start", "recheck")

    return _escalate_after_stream_side_exhausted(cfg, cooldowns, cd, reason_prefix=reason_prefix)


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

    if incident_class == IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_INACTIVE:
        # Checker UNREACHABLE and OBS not streaming — start stream (no capture device reset).
        key = "obs_start_stream"
        cool = float(cd.obs_start_stream)
        action = (
            RemediationAction.RUN_START_STREAM_SCRIPT
            if cfg.policy.prefer_script_for_stream_toggle
            else RemediationAction.OBS_START_STREAM_WEBSOCKET
        )
        if cooldowns.allowed(key, cool):
            return RemediationPlan(action, "public_unreachable_and_obs_not_streaming", key)
        return RemediationPlan(RemediationAction.RECHECK_ONLY, "cooldown_obs_start_stream_unreachable", "recheck")

    if incident_class == IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_ACTIVE:
        # Monitoring host UNREACHABLE while OBS streams — not a capture/device fault.
        grace_key = "public_recover_grace"
        if not cooldowns.allowed(grace_key, float(cd.public_recover_grace)):
            return RemediationPlan(RemediationAction.RECHECK_ONLY, "public_unreachable_monitoring_grace", "recheck")
        return RemediationPlan(
            RemediationAction.RECHECK_ONLY,
            "public_unreachable_obs_streaming_recheck_monitoring",
            "recheck",
        )

    if incident_class == IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE:
        # Public monitoring can lag substantially after OBS reports streaming active.
        grace_key = "public_recover_grace"
        if not cooldowns.allowed(grace_key, float(cd.public_recover_grace)):
            return RemediationPlan(RemediationAction.RECHECK_ONLY, "public_monitoring_lag_grace", "recheck")

        return _choose_stream_side_ladder(
            cfg,
            cooldowns,
            cd,
            reason_prefix="public_down_stream_active",
            capture_reason="public_down_but_obs_streaming_waited_grace_try_capture_reset",
            stop_start_reason="capture_reset_on_cooldown_try_controlled_restart",
        )

    if incident_class == IncidentClass.DEGRADED_SUSPECTED_CAPTURE:
        return _choose_stream_side_ladder(
            cfg,
            cooldowns,
            cd,
            reason_prefix="degraded_capture",
            capture_reason="degraded_capture_suspected",
            stop_start_reason="capture_reset_on_cooldown_try_controlled_restart",
        )

    if incident_class == IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE:
        key_retry = "obs_websocket_retry"
        if cooldowns.allowed(key_retry, float(cd.obs_websocket_retry)):
            return RemediationPlan(
                RemediationAction.RECHECK_ONLY,
                "ws_unreachable_retry_connection",
                key_retry,
            )

        key_api = "obs_control_api_restart"
        if cfg.obs_control_api is not None:
            # Restart obs64.exe via Windows control API (POST /obs/restart), not websocket stream toggle.
            if cooldowns.allowed(key_api, float(cd.obs_control_api_restart)):
                return RemediationPlan(
                    RemediationAction.RESTART_OBS_VIA_CONTROL_API,
                    "ws_unreachable_restart_obs_process_via_control_api",
                    key_api,
                )
            return RemediationPlan(
                RemediationAction.ESCALATE_OPERATOR,
                "ws_unreachable_obs_control_api_restart_on_cooldown",
                "escalate",
            )

        return RemediationPlan(
            RemediationAction.ESCALATE_OPERATOR,
            "ws_unreachable_obs_control_api_not_configured",
            "escalate",
        )

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
        cooldowns.touch(LADDER_OBS_API_DONE)
    elif action == RemediationAction.RUN_CAPTURE_DEVICES_RESET:
        result = scripts_wrapper.run_capture_devices_reset(cfg)
        cooldowns.touch("capture_reset")
        cooldowns.touch(LADDER_CAPTURE_DONE)
    elif action == RemediationAction.RUN_START_STREAM_SCRIPT:
        result = scripts_wrapper.run_start_stream_script(cfg)
        if result.exit_code == 0:
            cooldowns.touch("obs_start_stream")
            cooldowns.touch("public_recover_grace")
    elif action == RemediationAction.RUN_STOP_STREAM_SCRIPT:
        result = scripts_wrapper.run_stop_stream_script(cfg)
    elif action == RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS:
        # obs-cli scripts first: they can clear some stuck OBS states that a bare
        # WebSocket StartStream cannot. Fall back to WebSocket start+poll if the
        # script path fails or leaves output inactive (common StartStream 500 case).
        r1 = scripts_wrapper.run_stop_stream_script(cfg)
        time.sleep(2.0)
        r2 = scripts_wrapper.run_start_stream_script(cfg)

        stream_after = obs_wrapper.get_obs_stream_state(cfg)
        stream_ok = stream_after.output_active is True
        if r2.exit_code != 0 or not stream_ok:
            LOG.warning(
                "stop_then_start_script_failed_or_inactive_fallback_websocket",
                start_exit=r2.exit_code,
                output_active=stream_after.output_active,
                error=stream_after.error,
                stderr=(r2.stderr or "")[:500],
            )
            r3 = obs_wrapper.start_stream_websocket(cfg)
            stream_after = obs_wrapper.get_obs_stream_state(cfg)
            stream_ok = stream_after.output_active is True
            r2 = ScriptRunResult(
                name=f"{r2.name}+ws_fallback",
                exit_code=0 if stream_ok else (r3.exit_code or r2.exit_code or 1),
                stdout=(r2.stdout or "") + "\n" + (r3.stdout or ""),
                stderr=(r2.stderr or "") + "\n" + (r3.stderr or ""),
                elapsed_sec=r2.elapsed_sec + r3.elapsed_sec,
                command=list(r2.command) + list(r3.command),
            )

        exit_code = 0 if r1.exit_code == 0 and stream_ok else 1
        result = ScriptRunResult(
            name="stop_then_start",
            exit_code=exit_code,
            stdout=(
                f"stop:{r1.stdout}\nstart:{r2.stdout}\n"
                f"stream_output_active={stream_after.output_active!r} stream_error={stream_after.error!r}"
            ),
            stderr=f"stop:{r1.stderr}\nstart:{r2.stderr}",
            elapsed_sec=r1.elapsed_sec + r2.elapsed_sec,
            command=list(r1.command) + list(r2.command),
        )
        # Advance ladder even on failure so the next probe escalates instead of looping.
        cooldowns.touch("stream_stop_start")
        cooldowns.touch(LADDER_STOP_START_DONE)
        if result.exit_code == 0:
            cooldowns.touch("public_recover_grace")
        else:
            if not stream_ok:
                LOG.warning(
                    "stop_then_start_left_stream_inactive",
                    output_active=stream_after.output_active,
                    error=stream_after.error,
                )
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
