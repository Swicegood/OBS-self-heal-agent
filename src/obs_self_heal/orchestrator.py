from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from obs_self_heal.config import AppConfig, load_config, state_file_path
from obs_self_heal.cooldowns import CooldownStore
from obs_self_heal.logging_setup import get_logger
from obs_self_heal.models import (
    IncidentContext,
    ObsStreamState,
    ObsWebsocketHealth,
    PublicStreamHealth,
    ReachabilityResult,
    RemediationAction,
)
from obs_self_heal.policy import (
    choose_remediation,
    classify_incident,
    execute_remediation,
    verify_recovery,
)
from obs_self_heal.wrappers import obs as obs_wrapper
from obs_self_heal.wrappers import obs_control_api as obs_control_api_wrapper
from obs_self_heal.wrappers import reachability as reach
from obs_self_heal.wrappers import thruk as thruk_wrapper

LOG = get_logger("orchestrator")


@dataclass
class SignalSnapshot:
    """Collected or simulated signals for one cycle."""

    public: PublicStreamHealth
    ws: ObsWebsocketHealth
    stream: ObsStreamState
    obs_vm: ReachabilityResult | None
    unraid: ReachabilityResult | None


SignalCollector = Callable[[AppConfig], SignalSnapshot]


def default_collect_signals(cfg: AppConfig) -> SignalSnapshot:
    public = thruk_wrapper.check_public_stream_health(cfg)
    ws = obs_wrapper.check_obs_websocket(cfg)
    stream = obs_wrapper.get_obs_stream_state(cfg) if ws.reachable else ObsStreamState(output_active=None, error="ws_unreachable")
    obs_vm = reach.check_obs_vm_reachability(cfg)
    unraid = reach.check_unraid_reachability(cfg)
    # Optional evidence only (does not drive classification directly).
    try:
        if cfg.obs_control_api is not None:
            _ = obs_control_api_wrapper.get_control_api_status(cfg)
    except Exception:
        pass
    return SignalSnapshot(public=public, ws=ws, stream=stream, obs_vm=obs_vm, unraid=unraid)


def run_cycle(
    cfg: AppConfig,
    *,
    dry_run: bool | None = None,
    collector: SignalCollector | None = None,
    ctx: IncidentContext | None = None,
) -> dict[str, Any]:
    """One end-to-end classify → remediate → verify pass."""

    dry = cfg.dry_run_default if dry_run is None else dry_run
    ctx = ctx or IncidentContext(dry_run=dry, simulation=False)
    collector = collector or default_collect_signals

    cooldown_path = state_file_path(cfg)
    cooldowns = CooldownStore(cooldown_path)

    snap_before = collector(cfg)
    ws_ok = snap_before.ws.reachable

    classification = classify_incident(
        cfg,
        snap_before.public,
        ws_ok,
        snap_before.stream,
        snap_before.obs_vm,
        snap_before.unraid,
    )

    plan = choose_remediation(cfg, classification.incident_class, cooldowns)

    LOG.info(
        "incident_classified",
        incident_id=ctx.incident_id,
        incident_class=classification.incident_class.value,
        remediation=plan.action.value,
        reason=plan.reason,
        dry_run=dry,
    )

    exec_result: Any = None
    if cfg.policy.max_actions_per_incident <= 0:
        LOG.warning("max_actions_zero_skip")
    elif classification.incident_class.value == "healthy":
        pass
    elif ctx.actions_taken >= cfg.policy.max_actions_per_incident:
        LOG.warning("max_actions_per_incident_reached", limit=cfg.policy.max_actions_per_incident)
    else:
        exec_result = execute_remediation(cfg, ctx, plan.action, cooldowns, dry_run=dry)

    verify: dict[str, Any] = {}
    if classification.incident_class.value != "healthy" and plan.action != RemediationAction.NONE:
        time.sleep(cfg.policy.verify_delay_sec)
        snap_after = collector(cfg)
        verify = verify_recovery(cfg, snap_before.public, snap_after.public)
        verify["obs_ws_reachable_after"] = snap_after.ws.reachable
        verify["stream_active_after"] = snap_after.stream.output_active
        LOG.info("verify_recovery", incident_id=ctx.incident_id, **verify)
    else:
        verify = {"skipped": True}

    out: dict[str, Any] = {
        "incident_id": ctx.incident_id,
        "classification": classification.incident_class.value,
        "evidence": classification.evidence,
        "plan": {"action": plan.action.value, "reason": plan.reason, "cooldown_key": plan.cooldown_key},
        "dry_run": dry,
        "maintenance_mode": cfg.maintenance_mode,
        "execution": _serialize_exec(exec_result),
        "verify": verify,
        "signals_before": _serialize_signals(snap_before),
    }
    if snap_before.public.public_evaluation_delegated and snap_before.public.tac_html_excerpt:
        out["thruk_tac_html_for_agent"] = snap_before.public.tac_html_excerpt
        out["thruk_tac_html_truncated"] = snap_before.public.tac_html_truncated
    return out


def _serialize_exec(res: Any) -> dict[str, Any] | None:
    if res is None:
        return None
    if hasattr(res, "exit_code"):
        return {
            "name": res.name,
            "exit_code": res.exit_code,
            "stdout": res.stdout[:4000],
            "stderr": res.stderr[:4000],
            "elapsed_sec": res.elapsed_sec,
            "command": res.command,
        }
    return {"raw": str(res)}


def _serialize_signals(s: SignalSnapshot) -> dict[str, Any]:
    return {
        "public": {
            "ok": s.public.ok,
            "exit_code": s.public.exit_code,
            "critical": s.public.critical_count,
            "down": s.public.down_count,
            "parse_error": s.public.parse_error,
            "evaluation_delegated_to_openclaw": s.public.public_evaluation_delegated,
            "stdout_snip": (s.public.stdout or "")[:300],
            "stderr_snip": (s.public.stderr or "")[:300],
        },
        "ws": {"reachable": s.ws.reachable, "error": s.ws.error},
        "stream": {
            "output_active": s.stream.output_active,
            "scene": s.stream.current_program_scene,
            "error": s.stream.error,
        },
    }


def run_from_config_path(path: str, dry_run: bool | None = None) -> dict[str, Any]:
    cfg = load_config(path)
    return run_cycle(cfg, dry_run=dry_run)
