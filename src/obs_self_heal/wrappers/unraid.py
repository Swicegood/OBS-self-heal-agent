from __future__ import annotations

import re
import shlex
import time

from obs_self_heal.config import AppConfig
from obs_self_heal.models import ScriptRunResult, VmState
from obs_self_heal.wrappers.reachability import run_remote_command


_STATE_LINE = re.compile(r"^State:\s+(\S+)", re.MULTILINE)


def check_vm_state(cfg: AppConfig) -> VmState:
    """`virsh domstate <vm>` on unRAID host."""

    vm = shlex.quote(cfg.unraid.vm.name)
    res = run_remote_command(cfg, f"virsh domstate {vm}", timeout_sec=30.0)
    if res.exit_code != 0:
        return VmState(domain=vm, state=None, error=res.stderr.strip() or res.stdout.strip())
    m = _STATE_LINE.search(res.stdout)
    state = m.group(1) if m else res.stdout.strip().splitlines()[0] if res.stdout.strip() else None
    return VmState(domain=vm, state=state, error=None)


def restart_obs_vm(cfg: AppConfig) -> ScriptRunResult:
    """
    Power-cycle the domain: `virsh destroy` + `virsh start`.

    `virsh reset` is not sufficient for some stuck states (e.g., paused), while destroy/start
    consistently forces a state transition. This is high impact and must be gated by policy + cooldown.
    """

    vm = shlex.quote(cfg.unraid.vm.name)
    timeout = float(cfg.unraid.virsh.restart_domain_timeout_sec)
    start = time.perf_counter()

    destroy = run_remote_command(cfg, f"virsh destroy {vm}", timeout_sec=timeout)
    # If destroy fails because it's already off, we still try start.
    start_res = run_remote_command(cfg, f"virsh start {vm}", timeout_sec=max(1.0, timeout - (time.perf_counter() - start)))

    exit_code = 0 if (destroy.exit_code in (0, 1) and start_res.exit_code == 0) else (start_res.exit_code or destroy.exit_code)
    stdout = (destroy.stdout or "") + ("" if not destroy.stdout or destroy.stdout.endswith("\n") else "\n") + (start_res.stdout or "")
    stderr = (destroy.stderr or "") + ("" if not destroy.stderr or destroy.stderr.endswith("\n") else "\n") + (start_res.stderr or "")
    elapsed = time.perf_counter() - start

    return ScriptRunResult(
        name="ssh_remote_destroy_then_start",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        elapsed_sec=elapsed,
        command=[
            "ssh_remote",
            f"virsh destroy {vm}",
            f"virsh start {vm}",
        ],
    )


def verify_vm_recovered(cfg: AppConfig) -> VmState:
    """Re-check domain state after remediation."""

    return check_vm_state(cfg)
