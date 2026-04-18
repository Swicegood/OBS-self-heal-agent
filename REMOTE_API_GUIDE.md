OBS Control API — remote usage guide (for operators & LLM agents)

This document describes how to call the OBS Control API from another computer (e.g. a machine running Cursor / Composer 2). The API is a FastAPI app served by uvicorn, bound to **0.0.0.0:8765** on the Windows host that runs OBS.

Prerequisites on the Windows OBS machine

Python dependencies installed (pip install -r requirements.txt).

**config.json** present next to [app.py](http://app.py) (copy from config.example.json). Do not commit real tokens.

API process listening on port 8765 (manual uvicorn or scheduled task — see scripts/install-obs-api-scheduled-task.ps1).

Windows Firewall: allow inbound TCP 8765 (or remote calls will hang or time out).

Network: the other computer must reach the host’s IP (same LAN, VPN, or port-forward). Use the host’s LAN IP or DNS name, not 127.0.0.1, from the remote side.

Security model (read this first)

Transport: the stock setup uses HTTP, not HTTPS. Any machine on the path can read the token and body. Prefer a trusted network, VPN, or put a reverse proxy with TLS in front if exposed beyond a lab.

Authentication: every endpoint below requires header **x-api-token** matching **api_token** in config.json.

Unauthorized: missing or wrong token → 401 with {"detail":"Unauthorized"}.

Base URL

Replace OBS_HOST with the Windows machine’s reachable address:

[http://OBS_HOST:8765](http://OBS_HOST:8765)

Example: [http://192.168.1.50:8765](http://192.168.1.50:8765)

HTTP header (all requests)

x-api-token: <same value as api_token in config.json>

Endpoints

GET /health

Liveness check for the API itself (not OBS).

Response: 200 → {"ok":true,"service":"obs-control-api"}

GET /obs/status

JSON with OBS process info and WebSocket-derived stats (if OBS WebSocket is reachable).

Response (shape):

process_running (bool)

pid (number or null)

websocket — includes reachable, and when reachable, version/stats fields; on failure includes error string

POST /obs/start

Starts OBS if it is not already running.

JSON body (optional) — StartRequest:

Field

Type

Meaning

start_streaming

bool or omit

If omitted, uses start_streaming_on_launch from config

scene

string or omit

--scene

profile

string or omit

--profile

collection

string or omit

--collection

If OBS is already running: 200 with "started": false, "reason": "already running".

POST /obs/stop

Force-stops OBS (obs64.exe). Returns whether a process was stopped.

POST /obs/restart

Stops all obs64.exe instances, clears safe-mode / crash handling paths as implemented in [app.py](http://app.py), then starts OBS again.

Same optional JSON body as POST /obs/start (start_streaming, scene, profile, collection).

Use **"start_streaming": true** in the body when you want streaming after restart regardless of other defaults.

POST /scripts/capture-reset · POST /scripts/start-stream · POST /scripts/stop-stream

Runs configured PowerShell scripts from config.json (capture_reset_script, start_stream_script, stop_stream_script). If a path is empty or missing, the call will fail at runtime.

Response: returncode, stdout, stderr from the script run.

Examples (remote machine)

curl (Linux / macOS / WSL)

export OBS_API="[http://192.168.1.50:8765](http://192.168.1.50:8765)"

export OBS_TOKEN="your-api-token-from-config"

curl -sS -H "x-api-token: $OBS_TOKEN" "$OBS_API/health"

curl -sS -H "x-api-token: $OBS_TOKEN" "$OBS_API/obs/status"

curl -sS -X POST -H "x-api-token: $OBS_TOKEN" -H "Content-Type: application/json" \

  -d '{"start_streaming":true}' \

  "$OBS_API/obs/restart"

PowerShell (remote Windows)

$base = "[http://192.168.1.50:8765](http://192.168.1.50:8765)"

$h = @{ "x-api-token" = "your-api-token-from-config" }

Invoke-RestMethod -Uri "$base/health" -Headers $h

Invoke-RestMethod -Uri "$base/obs/restart" -Method Post -Headers $h -ContentType "application/json" -Body '{"start_streaming":true}'

Configuration notes (for correct behavior)

**obs_user_config_dir**: Set to the absolute ...\AppData\Roaming\obs-studio folder for the Windows user that runs OBS when the API runs as a service or SYSTEM; otherwise %APPDATA% may point at the wrong profile and safe-mode files may not be cleared.

**start_streaming_on_launch**: Default used when start_streaming is omitted in JSON.

**obs_websocket_password**: Must match OBS → Settings → WebSocket for /obs/status to report a reachable WebSocket.

Config is loaded once at process start. After editing config.json, restart the uvicorn process (or the scheduled task) so the token and paths apply.

Windows host: scheduled task (operator summary)

Task name: **OBS-self-heal API**.

Uses **pythonw** via **cmd.exe** with output appended to **logs\uvicorn-console.log** (see scripts/install-obs-api-scheduled-task.ps1).

Logon trigger is recommended so OBS runs in the interactive user session (visible UI). Startup/SYSTEM is for headless scenarios only.

If the API stops working after a change, check **logs\uvicorn-console.log**, **logs\obs_control_api.log**, and Task Scheduler Last run result — 0 means success.

Troubleshooting from another computer

Symptom

Likely cause

Connection hangs / timeout

Firewall on OBS host, wrong IP, or API not running

401 Unauthorized

Wrong x-api-token or API not restarted after token change

websocket.reachable: false

OBS not running, WebSocket disabled, or password mismatch

OBS starts but no window

API running as SYSTEM without interactive session; use Logon task

Quick checklist for an LLM agent

Confirm **GET /health** with **x-api-token** returns 200.

Use **GET /obs/status** before/after actions.

Use **POST /obs/restart** with body {"start_streaming":true} when recovery after crash is required.

Never assume HTTPS; use a secure network or add TLS elsewhere if needed.

