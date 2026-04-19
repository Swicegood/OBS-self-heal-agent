"""Command-line entrypoint for `obs-self-heal`."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Mapping

from obs_self_heal.config import load_config
from obs_self_heal.models import (
    IncidentClass,
    ObsStreamState,
    ObsWebsocketHealth,
    PublicStreamHealth,
    ReachabilityResult,
)
from obs_self_heal.orchestrator import SignalSnapshot, default_collect_signals, run_from_config_path
from obs_self_heal.policy import classify_incident


def _snapshot_from_sim(data: Mapping[str, Any]) -> SignalSnapshot:
    """Build a `SignalSnapshot` from JSON (tests / simulation)."""

    p = data["public"]
    public = PublicStreamHealth(
        ok=p["ok"],
        exit_code=p["exit_code"],
        stdout=p.get("stdout", ""),
        stderr=p.get("stderr", ""),
        critical_count=p.get("critical_count"),
        down_count=p.get("down_count"),
        warning_count=p.get("warning_count"),
        unreachable_count=p.get("unreachable_count"),
        parse_error=p.get("parse_error"),
    )
    w = data["ws"]
    ws = ObsWebsocketHealth(
        reachable=w["reachable"],
        error=w.get("error"),
        connect_attempts=int(w.get("connect_attempts", 0)),
    )
    s = data["stream"]
    stream = ObsStreamState(
        output_active=s.get("output_active"),
        error=s.get("error"),
    )
    obs_vm: ReachabilityResult | None = None
    if data.get("obs_vm"):
        vm = data["obs_vm"]
        tcp_raw = vm.get("tcp_ok") or {}
        tcp_ok = {int(k): bool(v) for k, v in tcp_raw.items()}
        obs_vm = ReachabilityResult(
            host=str(vm["host"]),
            ping_ok=vm.get("ping_ok"),
            tcp_ok=tcp_ok,
            error=vm.get("error"),
        )
    unraid: ReachabilityResult | None = None
    if data.get("unraid"):
        ur = data["unraid"]
        tcp_raw = ur.get("tcp_ok") or {}
        tcp_ok = {int(k): bool(v) for k, v in tcp_raw.items()}
        unraid = ReachabilityResult(
            host=str(ur["host"]),
            ping_ok=ur.get("ping_ok"),
            tcp_ok=tcp_ok,
            error=ur.get("error"),
        )
    return SignalSnapshot(public=public, ws=ws, stream=stream, obs_vm=obs_vm, unraid=unraid)


def _cmd_probe(config_path: str) -> int:
    cfg = load_config(config_path)
    snap = default_collect_signals(cfg)
    ws_ok = snap.ws.reachable
    classification = classify_incident(
        cfg,
        snap.public,
        ws_ok,
        snap.stream,
        snap.obs_vm,
        snap.unraid,
    )
    return 0 if classification.incident_class == IncidentClass.HEALTHY else 1


def _cmd_run(config_path: str, *, dry_run: bool | None) -> int:
    out = run_from_config_path(config_path, dry_run=dry_run)
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="obs-self-heal")
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="Exit 0 when classification is healthy, 1 otherwise.")
    p_probe.add_argument("--config", required=True, help="Path to YAML config (e.g. configs/local.yaml).")

    p_run = sub.add_parser("run", help="Run one classify → remediate → verify cycle; print JSON.")
    p_run.add_argument("--config", required=True, help="Path to YAML config.")
    p_run.add_argument("--dry-run", action="store_true", help="Force dry-run for this invocation.")

    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            return _cmd_probe(args.config)
        if args.command == "run":
            return _cmd_run(args.config, dry_run=True if args.dry_run else None)
    except OSError as e:
        print(f"obs-self-heal: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
