from __future__ import annotations

import re
import shlex

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
    Hard reset path: `virsh reset` or `destroy` + `start` depending on policy.
    MVP uses `virsh reset <domain>` — still high impact; must be gated by policy + cooldown.
    """

    vm = shlex.quote(cfg.unraid.vm.name)
    return run_remote_command(cfg, f"virsh reset {vm}", timeout_sec=cfg.unraid.virsh.restart_domain_timeout_sec)


def verify_vm_recovered(cfg: AppConfig) -> VmState:
    """Re-check domain state after remediation."""

    return check_vm_state(cfg)
