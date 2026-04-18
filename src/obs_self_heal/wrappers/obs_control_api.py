from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from obs_self_heal.config import AppConfig
from obs_self_heal.models import ScriptRunResult


def restart_obs_via_control_api(cfg: AppConfig) -> ScriptRunResult:
    """Second-resort intervention: restart OBS via the Windows-host control API."""

    if cfg.obs_control_api is None:
        return ScriptRunResult(
            name="obs_control_api_restart",
            exit_code=2,
            stdout="",
            stderr="obs_control_api_not_configured",
            elapsed_sec=0.0,
            command=["POST", "<unset>/obs/restart"],
        )

    api = cfg.obs_control_api
    url = api.base_url.rstrip("/") + "/obs/restart"
    body = {"start_streaming": bool(api.start_streaming_on_recovery)}
    data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        method="POST",
        data=data,
        headers={
            "x-api-token": api.api_token,
            "Content-Type": "application/json",
        },
    )

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=float(api.timeout_sec)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed = time.perf_counter() - start
            ok = 200 <= int(getattr(resp, "status", 200)) < 300
            return ScriptRunResult(
                name="obs_control_api_restart",
                exit_code=0 if ok else 1,
                stdout=raw,
                stderr="",
                elapsed_sec=elapsed,
                command=["POST", url, json.dumps(body)],
            )
    except urllib.error.HTTPError as e:
        elapsed = time.perf_counter() - start
        err_body = ""
        try:
            err_body = (e.read() or b"").decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return ScriptRunResult(
            name="obs_control_api_restart",
            exit_code=1,
            stdout=err_body,
            stderr=f"HTTPError {getattr(e, 'code', '')}: {e}",
            elapsed_sec=elapsed,
            command=["POST", url, json.dumps(body)],
        )
    except Exception as e:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        return ScriptRunResult(
            name="obs_control_api_restart",
            exit_code=1,
            stdout="",
            stderr=f"{type(e).__name__}: {e}",
            elapsed_sec=elapsed,
            command=["POST", url, json.dumps(body)],
        )


def get_control_api_status(cfg: AppConfig) -> dict[str, Any]:
    """Best-effort GET /obs/status for evidence/debugging."""

    if cfg.obs_control_api is None:
        return {"configured": False}

    api = cfg.obs_control_api
    url = api.base_url.rstrip("/") + "/obs/status"
    req = urllib.request.Request(url, method="GET", headers={"x-api-token": api.api_token})
    try:
        with urllib.request.urlopen(req, timeout=float(api.timeout_sec)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return {"configured": True, "ok": True, "status": json.loads(raw)}
            except Exception:
                return {"configured": True, "ok": True, "raw": raw}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "ok": False, "error": f"{type(e).__name__}: {e}"}

