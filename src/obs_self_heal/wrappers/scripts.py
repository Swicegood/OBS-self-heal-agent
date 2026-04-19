from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from obs_self_heal.config import AppConfig
from obs_self_heal.models import ScriptRunResult


def _run_script(cfg: AppConfig, path: str, name: str) -> ScriptRunResult:
    script = Path(path).expanduser()
    cmd = [cfg.scripts.shell_executable, str(script)]
    env = dict(os.environ)
    env.update(cfg.scripts.env or {})
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=cfg.scripts.timeout_sec,
        env=env,
        check=False,
    )
    elapsed = time.perf_counter() - start
    return ScriptRunResult(
        name=name,
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        elapsed_sec=elapsed,
        command=cmd,
    )


def run_capture_devices_reset(cfg: AppConfig) -> ScriptRunResult:
    return _run_script(cfg, cfg.scripts.capture_devices_reset, "capture_devices_reset")


def run_start_stream_script(cfg: AppConfig) -> ScriptRunResult:
    return _run_script(cfg, cfg.scripts.start_stream, "start_stream_script")


def run_stop_stream_script(cfg: AppConfig) -> ScriptRunResult:
    return _run_script(cfg, cfg.scripts.stop_stream, "stop_stream_script")
