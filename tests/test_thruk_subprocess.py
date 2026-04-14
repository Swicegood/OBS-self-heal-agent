"""Mocked subprocess for `check_public_stream_health`."""

from unittest.mock import MagicMock, patch

from obs_self_heal.config import AppConfig, ObsConfig, PolicyConfig, ReachabilityConfig, ScriptsConfig, ThrukConfig, UnraidConfig, UnraidSshConfig, UnraidVmConfig


def _cfg() -> AppConfig:
    return AppConfig(
        thruk=ThrukConfig(script_path="/fake/thruk_status.py"),
        obs=ObsConfig(host="10.0.0.1", password=""),
        scripts=ScriptsConfig(
            capture_devices_reset="/tmp/a",
            start_stream="/tmp/b",
            stop_stream="/tmp/c",
        ),
        reachability=ReachabilityConfig(),
        unraid=UnraidConfig(ssh=UnraidSshConfig(host="h"), vm=UnraidVmConfig(name="v")),
        policy=PolicyConfig(),
    )


@patch("obs_self_heal.wrappers.thruk.subprocess.run")
def test_check_public_stream_health_mocked(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="keyword hits (rough): CRITICAL=0 WARNING=0 DOWN=1 UNREACHABLE=0\n", stderr="")

    from obs_self_heal.wrappers.thruk import check_public_stream_health

    h = check_public_stream_health(_cfg())
    assert h.down_count == 1
    assert h.ok is True
    mock_run.assert_called_once()
