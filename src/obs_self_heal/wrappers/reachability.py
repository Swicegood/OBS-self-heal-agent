from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

from obs_self_heal.config import AppConfig, ReachHostConfig
from obs_self_heal.models import ReachabilityResult, ScriptRunResult


def _tcp_probe(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ping(host: str, count: int) -> bool:
    try:
        proc = subprocess.run(
            ["ping", "-c", str(count), "-W", "2", host],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _check_host(label: str, rcfg: ReachHostConfig | None) -> ReachabilityResult | None:
    if rcfg is None:
        return None
    host = rcfg.host
    ping_ok = _ping(host, rcfg.ping_count) if rcfg.ping_count > 0 else None
    tcp_ok: dict[int, bool] = {}
    for p in rcfg.tcp_ports:
        tcp_ok[p] = _tcp_probe(host, p)
    err = None
    if ping_ok is False and not any(tcp_ok.values()) and rcfg.tcp_ports:
        err = "ping_failed_and_tcp_failed"
    return ReachabilityResult(host=host, ping_ok=ping_ok, tcp_ok=tcp_ok, error=err)


def check_obs_vm_reachability(cfg: AppConfig) -> ReachabilityResult | None:
    return _check_host("obs_vm", cfg.reachability.obs_vm)


def check_unraid_reachability(cfg: AppConfig) -> ReachabilityResult | None:
    return _check_host("unraid", cfg.reachability.unraid)


def ssh_base_command(cfg: AppConfig) -> list[str]:
    ssh = cfg.unraid.ssh
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
    if ssh.identity_file:
        cmd.extend(["-i", str(Path(ssh.identity_file).expanduser())])
    cmd.extend(ssh.extra_args)
    cmd.append(f"{ssh.user}@{ssh.host}")
    return cmd


def run_remote_command(cfg: AppConfig, remote_cmd: str, timeout_sec: float = 60.0) -> ScriptRunResult:
    """Run a single remote shell command via SSH (audit-friendly)."""

    base = ssh_base_command(cfg)
    full = base + [remote_cmd]
    start = time.perf_counter()
    proc = subprocess.run(
        full,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    elapsed = time.perf_counter() - start
    return ScriptRunResult(
        name="ssh_remote",
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        elapsed_sec=elapsed,
        command=full,
    )
