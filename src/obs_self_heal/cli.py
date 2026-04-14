from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from obs_self_heal.config import load_config
from obs_self_heal.logging_setup import configure_logging, get_logger
from obs_self_heal.models import (
    IncidentContext,
    ObsStreamState,
    ObsWebsocketHealth,
    PublicStreamHealth,
    ReachabilityResult,
)
from obs_self_heal.orchestrator import SignalSnapshot, default_collect_signals, run_cycle


def _load_simulation(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_from_sim(data: dict[str, Any]) -> SignalSnapshot:
    p = data["public"]
    public = PublicStreamHealth(
        ok=p.get("ok", True),
        exit_code=int(p.get("exit_code", 0)),
        stdout=p.get("stdout", ""),
        stderr=p.get("stderr", ""),
        critical_count=p.get("critical_count"),
        down_count=p.get("down_count"),
        warning_count=p.get("warning_count"),
        unreachable_count=p.get("unreachable_count"),
        parse_error=p.get("parse_error"),
        elapsed_sec=float(p.get("elapsed_sec", 0.0)),
        public_evaluation_delegated=bool(p.get("public_evaluation_delegated", False)),
        tac_html_excerpt=p.get("tac_html_excerpt"),
        tac_html_truncated=bool(p.get("tac_html_truncated", False)),
    )
    w = data.get("ws", {})
    ws = ObsWebsocketHealth(
        reachable=bool(w.get("reachable", True)),
        error=w.get("error"),
        connect_attempts=int(w.get("connect_attempts", 1)),
        elapsed_sec=float(w.get("elapsed_sec", 0.0)),
    )
    st = data.get("stream", {})
    stream = ObsStreamState(
        output_active=st.get("output_active"),
        current_program_scene=st.get("current_program_scene"),
        error=st.get("error"),
    )

    def _reach(key: str) -> ReachabilityResult | None:
        if key not in data or data[key] is None:
            return None
        r = data[key]
        raw_tcp = r.get("tcp_ok") or {}
        return ReachabilityResult(
            host=r["host"],
            ping_ok=r.get("ping_ok"),
            tcp_ok={int(k): bool(v) for k, v in raw_tcp.items()},
            error=r.get("error"),
        )

    return SignalSnapshot(
        public=public,
        ws=ws,
        stream=stream,
        obs_vm=_reach("obs_vm"),
        unraid=_reach("unraid"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="obs-self-heal", description="OpenClaw-centered OBS self-healing CLI")
    parser.add_argument(
        "--config",
        default=os.environ.get("OBS_SELF_HEAL_CONFIG", "configs/local.yaml"),
        help="Path to YAML config (or OBS_SELF_HEAL_CONFIG)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run one evaluation/remediation cycle")
    run_p.add_argument("--dry-run", action="store_true", help="Log actions without executing mutating remediations")
    run_p.add_argument(
        "--simulation",
        type=str,
        default=None,
        help="JSON file describing SignalSnapshot (fixed signals, no live probes)",
    )

    args = parser.parse_args(argv)

    cfg_path = Path(args.config).expanduser()
    cfg = load_config(cfg_path)

    configure_logging(cfg.logging.level, json_format=cfg.logging.json_format)
    log = get_logger("cli")

    if args.cmd == "run":
        dry = bool(args.dry_run) or cfg.dry_run_default
        ctx = IncidentContext(dry_run=dry, simulation=bool(args.simulation))

        if args.simulation:
            sim_data = _load_simulation(Path(args.simulation).expanduser())
            snap = _snapshot_from_sim(sim_data)

            def collector(_cfg: Any) -> SignalSnapshot:
                return snap

            out = run_cycle(cfg, dry_run=dry, collector=collector, ctx=ctx)
        else:
            out = run_cycle(cfg, dry_run=dry, ctx=ctx)

        log.info("run_complete", result=out)
        print(json.dumps(out, default=str, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
