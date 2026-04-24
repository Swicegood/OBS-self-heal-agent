from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from obs_self_heal.config import AppConfig
from obs_self_heal.models import PublicStreamHealth


_KEYWORD_LINE = re.compile(
    r"CRITICAL=(?P<critical>\d+).*?WARNING=(?P<warning>\d+).*?DOWN=(?P<down>\d+).*?UNREACHABLE=(?P<unreachable>\d+)",
    re.DOTALL | re.IGNORECASE,
)


def parse_thruk_stdout(stdout: str) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    m = _KEYWORD_LINE.search(stdout)
    if not m:
        return None, None, None, None, "keyword_line_not_found"
    return (
        int(m.group("critical")),
        int(m.group("warning")),
        int(m.group("down")),
        int(m.group("unreachable")),
        None,
    )


def check_public_stream_health(cfg: AppConfig) -> PublicStreamHealth:
    """Public / Thruk health: either scoped TAC row parse (in-process) or `thruk_status.py` aggregate."""

    if any(s.enabled for s in (cfg.thruk.scopes or [])) or (cfg.thruk.scope is not None and cfg.thruk.scope.enabled):
        from obs_self_heal.wrappers.thruk_scoped import check_public_stream_health_scoped, check_public_stream_health_scoped_multi

        scopes = [s for s in (cfg.thruk.scopes or []) if s.enabled]
        if scopes:
            return check_public_stream_health_scoped_multi(cfg, scopes)
        return check_public_stream_health_scoped(cfg)

    script = Path(cfg.thruk.script_path).expanduser()
    env = {**dict(os.environ), **{k: str(Path(v).expanduser()) for k, v in cfg.thruk.env.items()}}

    cmd = [cfg.thruk.python_executable, str(script)]
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=float(cfg.thruk.script_timeout_sec),
        env=env,
        check=False,
    )
    elapsed = time.perf_counter() - start
    out = proc.stdout or ""
    err = proc.stderr or ""
    crit, warn, down, unr, perr = parse_thruk_stdout(out)
    return PublicStreamHealth(
        ok=proc.returncode == 0 and perr is None,
        exit_code=proc.returncode,
        stdout=out,
        stderr=err,
        critical_count=crit,
        down_count=down,
        warning_count=warn,
        unreachable_count=unr,
        parse_error=perr,
        elapsed_sec=elapsed,
    )
