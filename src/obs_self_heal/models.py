from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4


class IncidentClass(str, Enum):
    """High-level incident classification for policy."""

    HEALTHY = "healthy"
    PUBLIC_DOWN_OBS_REACHABLE_STREAM_INACTIVE = "public_down_obs_reachable_stream_inactive"
    PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE = "public_down_obs_reachable_stream_active"
    DEGRADED_SUSPECTED_CAPTURE = "degraded_suspected_capture"
    OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE = "obs_websocket_unreachable_vm_reachable"
    VM_OR_NETWORK_UNHEALTHY = "vm_or_network_unhealthy"
    UNKNOWN = "unknown"


class RemediationAction(str, Enum):
    """Concrete remediation steps (adapters must implement these explicitly)."""

    NONE = "none"
    RECHECK_ONLY = "recheck_only"
    OBS_START_STREAM_WEBSOCKET = "obs_start_stream_websocket"
    OBS_STOP_STREAM_WEBSOCKET = "obs_stop_stream_websocket"
    RUN_CAPTURE_DEVICES_RESET = "run_capture_devices_reset"
    RUN_STOP_STREAM_SCRIPT = "run_stop_stream_script"
    RUN_START_STREAM_SCRIPT = "run_start_stream_script"
    RUN_STOP_THEN_START_STREAM_SCRIPTS = "run_stop_then_start_stream_scripts"
    RESTART_OBS_VIA_CONTROL_API = "restart_obs_via_control_api"
    RESTART_OBS_VM = "restart_obs_vm"
    ESCALATE_OPERATOR = "escalate_operator"


@dataclass
class PublicStreamHealth:
    """Result of wrapping `thruk_status.py` (aggregate tactical signal)."""

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    critical_count: int | None = None
    down_count: int | None = None
    warning_count: int | None = None
    unreachable_count: int | None = None
    parse_error: str | None = None
    elapsed_sec: float = 0.0
    # When True, policy does not use keyword counts; `tac_html_excerpt` is for OpenClaw to interpret.
    public_evaluation_delegated: bool = False
    tac_html_excerpt: str | None = None
    tac_html_truncated: bool = False

    def is_public_healthy(self, critical_threshold: int, treat_down_as_unhealthy: bool) -> bool:
        if self.public_evaluation_delegated:
            return True
        if self.exit_code != 0 or self.parse_error:
            return False
        if self.critical_count is not None and self.critical_count >= critical_threshold:
            return False
        if treat_down_as_unhealthy and (self.down_count or 0) > 0:
            return False
        return True

    def is_degraded(self, warning_only_is_degraded: bool) -> bool:
        if self.public_evaluation_delegated:
            return False
        if not warning_only_is_degraded:
            return False
        return (self.warning_count or 0) > 0 and (self.critical_count or 0) == 0 and (self.down_count or 0) == 0


@dataclass
class ObsWebsocketHealth:
    reachable: bool
    error: str | None = None
    connect_attempts: int = 0
    elapsed_sec: float = 0.0


@dataclass
class ObsStreamState:
    output_active: bool | None
    output_bytes: int | None = None
    output_duration_ms: int | None = None
    current_program_scene: str | None = None
    error: str | None = None


@dataclass
class ObsStats:
    """Subset of OBS stats for logging and policy hints."""

    raw: Mapping[str, Any] = field(default_factory=dict)
    cpu_usage: float | None = None
    memory_usage: float | None = None
    available_disk_space: str | None = None
    average_frame_time: float | None = None
    render_skipped_frames: int | None = None
    output_skipped_frames: int | None = None


@dataclass
class ReachabilityResult:
    host: str
    ping_ok: bool | None = None
    tcp_ok: dict[int, bool] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ScriptRunResult:
    name: str
    exit_code: int
    stdout: str
    stderr: str
    elapsed_sec: float
    command: list[str]


@dataclass
class VmState:
    """Placeholder / parsed virsh state."""

    domain: str
    state: str | None = None
    error: str | None = None


@dataclass
class IncidentContext:
    """Single evaluation / remediation cycle."""

    incident_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dry_run: bool = False
    simulation: bool = False
    actions_taken: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    def bump_actions(self, n: int = 1) -> None:
        self.actions_taken += n
