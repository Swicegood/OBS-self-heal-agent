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


def test_classify_public_unreachable_stream_active() -> None:
    cfg = _minimal_cfg()
    pub = PublicStreamHealth(
        ok=True,
        exit_code=0,
        stdout="service_state: UNREACHABLE",
        stderr="",
        critical_count=0,
        down_count=0,
        unreachable_count=1,
    )
    r = classify_incident(
        cfg,
        pub,
        True,
        ObsStreamState(output_active=True),
        ReachabilityResult(host="h", ping_ok=True, tcp_ok={4455: True}),
        None,
    )
    assert r.incident_class == IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_ACTIVE
    assert r.evidence.get("unreachable_count") == 1


def test_classify_public_unreachable_stream_inactive() -> None:
    cfg = _minimal_cfg()
    pub = PublicStreamHealth(
        ok=True,
        exit_code=0,
        stdout="service_state: UNREACHABLE",
        stderr="",
        critical_count=0,
        down_count=0,
        unreachable_count=1,
    )
    r = classify_incident(
        cfg,
        pub,
        True,
        ObsStreamState(output_active=False),
        ReachabilityResult(host="h", ping_ok=True, tcp_ok={4455: True}),
        None,
    )
    assert r.incident_class == IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_INACTIVE


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


def test_choose_remediation_public_unreachable_inactive_starts_stream(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    store = CooldownStore(tmp_path / "cd_public_unr_inactive.json")
    plan = choose_remediation(
        cfg,
        IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_INACTIVE,
        store,
    )
    assert plan.action == RemediationAction.OBS_START_STREAM_WEBSOCKET
    assert plan.action != RemediationAction.RUN_CAPTURE_DEVICES_RESET


def test_choose_remediation_public_unreachable_active_recheck_only(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    store = CooldownStore(tmp_path / "cd_public_unr_active.json")
    plan = choose_remediation(
        cfg,
        IncidentClass.PUBLIC_UNREACHABLE_OBS_REACHABLE_STREAM_ACTIVE,
        store,
    )
    assert plan.action == RemediationAction.RECHECK_ONLY
    assert plan.action != RemediationAction.RUN_CAPTURE_DEVICES_RESET


def test_choose_remediation_public_down_stream_active_capture_reset_after_grace(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    store = CooldownStore(tmp_path / "cd_public_active.json")
    plan = choose_remediation(cfg, IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE, store)
    assert plan.action == RemediationAction.RUN_CAPTURE_DEVICES_RESET


def test_choose_remediation_public_down_stream_active_advances_past_cooled_capture(tmp_path: Path) -> None:
    """After capture was tried, do not retry it when short cooldown expires — advance ladder."""
    cfg = _minimal_cfg()
    cfg.policy.allow_vm_restart = True
    store = CooldownStore(tmp_path / "cd_ladder_advance.json")
    store.touch("ladder_capture_done")
    # Short capture cooldown already expired (not present) — still must not redo capture.
    plan = choose_remediation(cfg, IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE, store)
    assert plan.action == RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS


def test_choose_remediation_public_down_stream_active_escalates_to_vm(tmp_path: Path) -> None:
    """After capture + stop/start ladder steps, escalate even if short cooldowns expired."""
    cfg = _minimal_cfg()
    cfg.policy.allow_vm_restart = True
    store = CooldownStore(tmp_path / "cd_public_active_escalate.json")
    store.touch("ladder_capture_done")
    store.touch("ladder_stop_start_done")
    plan = choose_remediation(cfg, IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE, store)
    assert plan.action == RemediationAction.RESTART_OBS_VM
    assert "vm_restart" in plan.reason


def test_choose_remediation_public_down_stream_active_escalates_to_obs_api_first(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    cfg.policy.allow_vm_restart = True
    cfg.obs_control_api = ObsControlApiConfig(base_url="http://10.0.0.9:8765", api_token="t")
    store = CooldownStore(tmp_path / "cd_public_active_api.json")
    store.touch("ladder_capture_done")
    store.touch("ladder_stop_start_done")
    plan = choose_remediation(cfg, IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE, store)
    assert plan.action == RemediationAction.RESTART_OBS_VIA_CONTROL_API


def test_choose_remediation_public_down_stream_active_vm_after_api_ladder_step(tmp_path: Path) -> None:
    """API sticky progress must prevent re-trying API forever across slow probes."""
    cfg = _minimal_cfg()
    cfg.policy.allow_vm_restart = True
    cfg.obs_control_api = ObsControlApiConfig(base_url="http://10.0.0.9:8765", api_token="t")
    store = CooldownStore(tmp_path / "cd_public_active_api_then_vm.json")
    store.touch("ladder_capture_done")
    store.touch("ladder_stop_start_done")
    store.touch("ladder_obs_api_done")
    plan = choose_remediation(cfg, IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE, store)
    assert plan.action == RemediationAction.RESTART_OBS_VM


def test_choose_remediation_public_down_stream_active_escalates_operator_when_vm_disabled(
    tmp_path: Path,
) -> None:
    cfg = _minimal_cfg()
    store = CooldownStore(tmp_path / "cd_public_active_op.json")
    store.touch("ladder_capture_done")
    store.touch("ladder_stop_start_done")
    plan = choose_remediation(cfg, IncidentClass.PUBLIC_DOWN_OBS_REACHABLE_STREAM_ACTIVE, store)
    assert plan.action == RemediationAction.ESCALATE_OPERATOR
    assert plan.action != RemediationAction.RECHECK_ONLY


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


def test_choose_remediation_ws_unreachable_retries_before_api(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    cfg.obs_control_api = ObsControlApiConfig(base_url="http://10.0.0.9:8765", api_token="t")
    store = CooldownStore(tmp_path / "cd3.json")
    plan = choose_remediation(cfg, IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE, store)
    assert plan.action == RemediationAction.RECHECK_ONLY
    assert plan.cooldown_key == "obs_websocket_retry"


def test_choose_remediation_ws_unreachable_api_restart_after_retry(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    cfg.obs_control_api = ObsControlApiConfig(base_url="http://10.0.0.9:8765", api_token="t")
    store = CooldownStore(tmp_path / "cd3b.json")
    store.touch("obs_websocket_retry")
    plan = choose_remediation(cfg, IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE, store)
    assert plan.action == RemediationAction.RESTART_OBS_VIA_CONTROL_API


def test_choose_remediation_ws_unreachable_no_stream_toggle_without_api(tmp_path: Path) -> None:
    cfg = _minimal_cfg()
    store = CooldownStore(tmp_path / "cd3c.json")
    store.touch("obs_websocket_retry")
    plan = choose_remediation(cfg, IncidentClass.OBS_WEBSOCKET_UNREACHABLE_VM_REACHABLE, store)
    assert plan.action == RemediationAction.ESCALATE_OPERATOR
    assert plan.action != RemediationAction.RUN_STOP_THEN_START_STREAM_SCRIPTS
