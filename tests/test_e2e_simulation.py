"""End-to-end simulated incident (fixed signals, dry-run)."""

import json
from pathlib import Path

from obs_self_heal.config import load_config
from obs_self_heal.models import (
    IncidentContext,
    ObsStreamState,
    ObsWebsocketHealth,
    PublicStreamHealth,
    ReachabilityResult,
)
from obs_self_heal.orchestrator import SignalSnapshot, run_cycle


def _snapshot_public_down_inactive() -> SignalSnapshot:
    return SignalSnapshot(
        public=PublicStreamHealth(
            ok=True,
            exit_code=0,
            stdout="keyword hits (rough): CRITICAL=0 WARNING=0 DOWN=1 UNREACHABLE=0",
            stderr="",
            critical_count=0,
            down_count=1,
        ),
        ws=ObsWebsocketHealth(reachable=True),
        stream=ObsStreamState(output_active=False),
        obs_vm=ReachabilityResult(host="10.0.0.1", ping_ok=True, tcp_ok={4455: True}),
        unraid=None,
    )


def test_simulated_cycle_dry_run(tmp_path: Path) -> None:
    cfg_path = Path(__file__).resolve().parent / "fixtures" / "minimal_config.yaml"
    cfg = load_config(cfg_path)

    snap = _snapshot_public_down_inactive()

    def collector(_cfg: object) -> SignalSnapshot:
        return snap

    out = run_cycle(cfg, dry_run=True, collector=collector, ctx=IncidentContext(dry_run=True, simulation=True))
    assert out["classification"] == "public_down_obs_reachable_stream_inactive"
    assert out["dry_run"] is True
    exec_payload = out.get("execution") or {}
    assert exec_payload.get("stdout") == "skipped_dry_run_or_maintenance"
    assert out["plan"]["action"] == "obs_start_stream_websocket"


def test_cli_simulation_json_roundtrip(tmp_path: Path) -> None:
    from obs_self_heal.cli import _snapshot_from_sim

    fixture = Path(__file__).resolve().parent / "fixtures" / "sim_snapshot.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    snap = _snapshot_from_sim(data)
    assert snap.public.down_count == 1
    assert snap.ws.reachable is True
