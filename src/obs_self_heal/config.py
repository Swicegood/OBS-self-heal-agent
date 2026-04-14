from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            key, default = m.group(1), m.group(2)
            return os.environ.get(key, default if default is not None else "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _expand_user_path(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expanduser(value)
    if isinstance(value, dict):
        return {k: _expand_user_path(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_user_path(v) for v in value]
    return value


class ThrukScopeConfig(BaseModel):
    """When enabled, fetch Thruk TAC in-process and count only table rows matching
    both `service_substring` and at least one `host_substrings` entry. Does not
    modify the lan-monitoring `thruk_status.py` script."""

    enabled: bool = False
    # Preferred deterministic selectors (OMD objects).
    # If set, the scoped checker will fetch the service detail page and extract status from it.
    host_name: str = ""
    service_name: str = ""
    # Legacy / fallback selectors used for TAC-page heuristics when exact names aren't set.
    service_substring: str = ""
    host_substrings: list[str] = Field(default_factory=list)
    # Chars of context on each side of the service name when using proximity fallback
    # (Thruk often splits host/service across `<tr>` or tags so strict `<tr>` match fails).
    proximity_window_chars: int = 4500
    # Skip deterministic keyword policy; attach TAC HTML for an OpenClaw agent to interpret.
    delegate_public_to_openclaw: bool = False
    openclaw_tac_html_max_chars: int = 120_000


class ThrukConfig(BaseModel):
    script_path: str
    python_executable: str = "python3"
    env: dict[str, str] = Field(default_factory=dict)
    down_unhealthy: bool = True
    critical_threshold: int = 1
    warning_only_is_degraded: bool = False
    scope: ThrukScopeConfig | None = None


class ObsConfig(BaseModel):
    host: str
    port: int = 4455
    password: str = ""
    timeout_sec: float = 10.0
    connect_retries: int = 2
    retry_delay_sec: float = 1.5
    expected_streaming_when_healthy: bool = True


class ReachHostConfig(BaseModel):
    host: str
    ping_count: int = 1
    tcp_ports: list[int] = Field(default_factory=list)


class ReachabilityConfig(BaseModel):
    obs_vm: ReachHostConfig | None = None
    unraid: ReachHostConfig | None = None


class ScriptsConfig(BaseModel):
    capture_devices_reset: str
    start_stream: str
    stop_stream: str
    shell_executable: str = "/bin/bash"
    timeout_sec: float = 600.0


class UnraidSshConfig(BaseModel):
    host: str
    user: str = "root"
    identity_file: str | None = None
    extra_args: list[str] = Field(default_factory=list)


class UnraidVmConfig(BaseModel):
    name: str


class UnraidVirshConfig(BaseModel):
    restart_domain_timeout_sec: float = 120.0


class UnraidConfig(BaseModel):
    ssh: UnraidSshConfig
    vm: UnraidVmConfig
    virsh: UnraidVirshConfig = Field(default_factory=UnraidVirshConfig)


class PolicyCooldownConfig(BaseModel):
    recheck: int = 30
    obs_websocket_retry: int = 15
    capture_reset: int = 300
    stream_stop_start: int = 120
    obs_start_stream: int = 60
    vm_restart: int = 900


class PolicyConfig(BaseModel):
    cooldown_sec: PolicyCooldownConfig = Field(default_factory=PolicyCooldownConfig)
    max_actions_per_incident: int = 3
    verify_delay_sec: float = 20.0
    allow_vm_restart: bool = False
    prefer_script_for_stream_toggle: bool = False


class LoggingConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    level: str = "INFO"
    json_format: bool = Field(True, alias="json")


class AppConfig(BaseModel):
    maintenance_mode: bool = False
    dry_run_default: bool = False
    simulation_mode: bool = False
    state_dir: str = "~/.cache/obs-self-heal/state"

    thruk: ThrukConfig
    obs: ObsConfig
    reachability: ReachabilityConfig = Field(default_factory=ReachabilityConfig)
    scripts: ScriptsConfig
    unraid: UnraidConfig
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    model_config = {"extra": "ignore"}


def load_config(path: str | Path) -> AppConfig:
    p = Path(path).expanduser()
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    data = _expand_user_path(_expand_env(raw))
    return AppConfig.model_validate(data)


def state_file_path(cfg: AppConfig) -> Path:
    d = Path(cfg.state_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d / "cooldowns.json"
