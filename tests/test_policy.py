from pathlib import Path

from obs_self_heal.config import (
    AppConfig,
    ObsConfig,
    ObsControlApiConfig,
    PolicyConfig,
    ReachHostConfig,
    ReachabilityConfig,
    ScriptsConfig,
    ThrukConfig,
    UnraidConfig,
    UnraidSshConfig,
    UnraidVmConfig,
)
from obs_self_heal.cooldowns import CooldownStore
from obs_self_heal.models import IncidentClass, ObsStreamState, PublicStreamHealth, ReachabilityResult, RemediationAction
from obs_self_heal.policy import choose_remediation, classify_incident


def _minimal_cfg() -> AppConfig:
    return AppConfig(
        thruk=ThrukConfig(
            script_path="/tmp/thruk_status.py",
            critical_threshold=1,
            down_unhealthy=True,
        ),
        obs=ObsConfig(host="10.0.0.1", port=4455, password="x"),
        scripts=ScriptsConfig(
            capture_devices_reset="/tmp/cap.sh",
            start_stream="/tmp/start.sh",
            stop_stream="/tmp/stop.sh",
        ),
        reachability=ReachabilityConfig(
            obs_vm=ReachHostConfig(host="10.0.0.1", ping_count=1, tcp_ports=[4455]),
        ),
        unraid=UnraidConfig(
            ssh=UnraidSshConfig(host="10.0.0.2"),
            vm=UnraidVmConfig(name="obs"),
        ),
        policy=PolicyConfig(max_actions_per_incident=3, allow_vm_restart=False),
    )


def test_classify_public_delegated_to_openclaw_is_healthy_for_automation() -> None:
    """Deterministic counts ignored; agent reviews `thruk_tac_html_for_agent` in JSON."""
    cfg = _minimal_cfg()
    pub = PublicStreamHealth(
        ok=True,
        exit_code=0,
        stdout="delegated",
        stderr="",
        critical_count=99,
        down_count=99,
        public_evaluation_delegated=True,
        tac_html_excerpt="<html>...</html>",
    )
    r = classify_incident(
        cfg,
        pub,
        True,
        ObsStreamState(output_active=True),
        ReachabilityResult(host="h", ping_ok=True, tcp_ok={4455: True}),
        None,
    )
    assert r.incident_class == IncidentClass.HEALTHY
    assert r.evidence.get("public_evaluation_delegated") is True


def test_classify_healthy() -> None:
    cfg = _minimal_cfg()
    pub = PublicStreamHealth(
        ok=True,
        exit_code=0,
        stdout="keyword hits (rough): CRITICAL=0 WARNING=0 DOWN=0 UNREACHABLE=0",
        stderr="",
        critical_count=0,
        down_count=0,
    )
    r = classify_incident(
        cfg,
        pub,
        True,
        ObsStreamState(output_active=True),
        ReachabilityResult(host="h", ping_ok=True, tcp_ok={4455: True}),
        None,
    )
    assert r.incident_class == IncidentClass.HEALTHY


def test_public_down_stream_inactive() -> None:
    cfg = _minimal_cfg()
    pub = PublicStreamHealth(
        ok=True,
        exit_code=0,
        stdout="keyword hits (rough): CRITICAL=0 WARNING=0 DOWN=1 UNREACHABLE=0",
        stderr="",
        critical_count=0,
        down_count=1,
    )
    r = classify_incident(
        cfg,
        pub,
        True,
        ObsStreamState(output_active=False),
        ReachabilityResult(host="h", ping_ok=True, tcp_ok={4455: True}),
        None,
    )
    assert r.incident_class == IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_INACTIVE


def test_public_down_stream_active() -> None:
    cfg = _minimal_cfg()
    pub = PublicStreamHealth(
        ok=True,
        exit_code=0,
        stdout="keyword hits (rough): CRITICAL=0 WARNING=0 DOWN=1 UNREACHABLE=0",
        stderr="",
        critical_count=0,
        down_count=1,
    )
    r = classify_incident(
        cfg,
        pub,
        True,
        ObsStreamState(output_active=True),
        ReachabilityResult(host="h", ping_ok=True, tcp_ok={4455: True}),
        None,
    )
    assert r.incident_class == IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE


def test_ws_unreachable_vm_ok() -> None:
    cfg = _minimal_cfg()
    pub = PublicStreamHealth(
        ok=True,
        exit_code=0,
        stdout="keyword hits (rough): CRITICAL=0 WARNING=0 DOWN=1 UNREACHABLE=0",
        stderr="",
        critical_count=0,
        down_count=1,
    )
    r = classify_incident(
        cfg,
        pub,
        False,
        ObsStreamState(output_active=None, error="ws_unreachable"),
        ReachabilityResult(host="h", ping_ok=True, tcp_ok={4455: True}),
        None,
    )
    assert r.incident_class == IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE


def test_choose_remediation_stream_inactive(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    store = CooldownStore(tmp_path / "cd.json")
    plan = choose_remediation(cfg, IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_INACTIVE, store)
    assert plan.action == RemediationAction.OBS_START_STREAM_WEBSOCKET


def test_choose_remediation_vm_bad_no_restart(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    store = CooldownStore(tmp_path / "cd2.json")
    plan = choose_remediation(cfg, IncidentClass.VM_OR_NETWORK_UNHEALTHY, store)
    assert plan.action == RemediationAction.ESCALATE_OPERATOR


def test_choose_remediation_ws_unreachable_prefers_control_api_when_configured(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    cfg.obs_control_api = ObsControlApiConfig(base_url="http://10.0.0.9:8765", api_token="t")
    store = CooldownStore(tmp_path / "cd3.json")
    plan = choose_remediation(cfg, IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE, store)
    assert plan.action == RemediationAction.RESTART_OBS_VIA_CONTROL_API
