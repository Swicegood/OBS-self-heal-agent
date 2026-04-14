"""
Stable entrypoints for OpenClaw skills and external orchestrators.

Prefer importing from here rather than deep internal modules.
"""

from __future__ import annotations

from obs_self_heal.config import AppConfig
from obs_self_heal.models import ObsStats, ObsStreamState, PublicStreamHealth, ReachabilityResult, ScriptRunResult, VmState
from obs_self_heal.policy import (
    choose_remediation,
    classify_incident,
    execute_remediation,
    verify_recovery,
)
from obs_self_heal.wrappers.obs import check_obs_websocket, get_obs_stats, get_obs_stream_state
from obs_self_heal.wrappers.reachability import check_obs_vm_reachability, check_unraid_reachability
from obs_self_heal.wrappers.scripts import (
    run_capture_devices_reset,
    run_start_stream_script,
    run_stop_stream_script,
)
from obs_self_heal.wrappers.thruk import check_public_stream_health
from obs_self_heal.wrappers.unraid import check_vm_state, restart_obs_vm, verify_vm_recovered

__all__ = [
    "check_public_stream_health",
    "check_obs_websocket",
    "get_obs_stream_state",
    "get_obs_stats",
    "check_obs_vm_reachability",
    "check_unraid_reachability",
    "run_capture_devices_reset",
    "run_start_stream_script",
    "run_stop_stream_script",
    "check_vm_state",
    "restart_obs_vm",
    "verify_vm_recovered",
    "classify_incident",
    "choose_remediation",
    "execute_remediation",
    "verify_recovery",
    "AppConfig",
    "PublicStreamHealth",
    "ObsStreamState",
    "ObsStats",
    "ReachabilityResult",
    "ScriptRunResult",
    "VmState",
]
