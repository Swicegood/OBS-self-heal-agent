from __future__ import annotations

import time
from typing import Any

from obs_self_heal.config import AppConfig
from obs_self_heal.models import ObsStats, ObsStreamState, ObsWebsocketHealth, ScriptRunResult
from obs_self_heal.wrappers.obs_ws_client import ObsWebSocketV5


def check_obs_websocket(cfg: AppConfig) -> ObsWebsocketHealth:
    last_err: str | None = None
    attempts = 0
    start = time.perf_counter()
    for i in range(cfg.obs.connect_retries + 1):
        attempts += 1
        try:
            with ObsWebSocketV5(cfg.obs) as _ws:
                pass
            elapsed = time.perf_counter() - start
            return ObsWebsocketHealth(reachable=True, connect_attempts=attempts, elapsed_sec=elapsed)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            if i < cfg.obs.connect_retries:
                time.sleep(cfg.obs.retry_delay_sec)
    elapsed = time.perf_counter() - start
    return ObsWebsocketHealth(reachable=False, error=last_err, connect_attempts=attempts, elapsed_sec=elapsed)


def get_obs_stream_state(cfg: AppConfig) -> ObsStreamState:
    try:
        with ObsWebSocketV5(cfg.obs) as ws:
            st = ws.request("GetStreamStatus")
            active = bool(st.get("outputActive")) if isinstance(st, dict) else None
            out_bytes = int(st["outputBytes"]) if st.get("outputBytes") is not None else None
            dur = int(st["outputDuration"]) if st.get("outputDuration") is not None else None
            scene = None
            try:
                cur = ws.request("GetCurrentProgramScene")
                if isinstance(cur, dict):
                    scene = cur.get("currentProgramSceneName") or cur.get("sceneName")
            except Exception:  # noqa: BLE001
                scene = None
            return ObsStreamState(
                output_active=active,
                output_bytes=out_bytes,
                output_duration_ms=dur,
                current_program_scene=scene,
            )
    except Exception as e:  # noqa: BLE001
        return ObsStreamState(output_active=None, error=str(e))


def get_obs_stats(cfg: AppConfig) -> ObsStats:
    try:
        with ObsWebSocketV5(cfg.obs) as ws:
            st = ws.request("GetStats")
            if not isinstance(st, dict):
                return ObsStats(raw={"response": st})
            cpu = st.get("cpuUsage")
            mem = st.get("memoryUsage")
            disk = st.get("availableDiskSpace")
            aft = st.get("averageFrameTime")
            rsf = st.get("renderSkippedFrames")
            osf = st.get("outputSkippedFrames")
            return ObsStats(
                raw=st,
                cpu_usage=float(cpu) if cpu is not None else None,
                memory_usage=float(mem) if mem is not None else None,
                available_disk_space=str(disk) if disk is not None else None,
                average_frame_time=float(aft) if aft is not None else None,
                render_skipped_frames=int(rsf) if rsf is not None else None,
                output_skipped_frames=int(osf) if osf is not None else None,
            )
    except Exception as e:  # noqa: BLE001
        return ObsStats(raw={"error": str(e)})


def start_stream_websocket(cfg: AppConfig) -> ScriptRunResult:
    cmd = ["StartStream"]
    start = time.perf_counter()
    try:
        with ObsWebSocketV5(cfg.obs) as ws:
            ws.request("StartStream")
        elapsed = time.perf_counter() - start
        return _script_result("obs_start_stream_ws", 0, "OK", "", elapsed, cmd)
    except Exception as e:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        return _script_result("obs_start_stream_ws", 1, "", str(e), elapsed, cmd)


def stop_stream_websocket(cfg: AppConfig) -> ScriptRunResult:
    cmd = ["StopStream"]
    start = time.perf_counter()
    try:
        with ObsWebSocketV5(cfg.obs) as ws:
            ws.request("StopStream")
        elapsed = time.perf_counter() - start
        return _script_result("obs_stop_stream_ws", 0, "OK", "", elapsed, cmd)
    except Exception as e:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        return _script_result("obs_stop_stream_ws", 1, "", str(e), elapsed, cmd)


def _script_result(
    name: str,
    code: int,
    out: str,
    err: str,
    elapsed: float,
    cmd: list[str],
) -> ScriptRunResult:
    return ScriptRunResult(name=name, exit_code=code, stdout=out, stderr=err, elapsed_sec=elapsed, command=cmd)
