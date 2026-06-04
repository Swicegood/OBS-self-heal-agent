# Failure-mode matrix (MVP)

Legend: **Ext** = external/public (Thruk aggregate), **WS** = obs-websocket reachable, **Str** = OBS reports streaming active, **VM** = OBS VM network healthy (ping/TCP as configured).

| ID | Ext | WS | Str | VM | Class | Typical cause | First remediation (ladder) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1 | OK | OK | expected | OK | `healthy` | Nominal | None |
| F1 | DOWN | OK | inactive | OK | `public_down_obs_reachable_stream_inactive` | Output not started, profile issue | Confirm → OBS stream start (API or script) |
| F2 | DOWN | OK | active | OK | `public_down_obs_reachable_stream_active` | Stuck path, capture wedged, CDN/downstream | Evidence → capture reset → controlled stream restart |
| F6 | UNR | OK | inactive | OK | `public_unreachable_obs_reachable_stream_inactive` | Checker UNREACHABLE; OBS not streaming | Start stream (no capture reset) |
| F7 | UNR | OK | active | OK | `public_unreachable_obs_reachable_stream_active` | Checker UNREACHABLE while OBS streams | Recheck only (no capture reset) |
| F3 | DOWN | NO | * | OK | `obs_websocket_unreachable_vm_reachable` | OBS wedge, WS off, firewall | Retry WS → **POST /obs/restart** (control API) → escalate (no stream toggle) |
| F4 | DOWN | NO | * | BAD | `vm_or_network_unhealthy` | VM/network/host issue | Infrastructure probes → SSH/virsh per policy |
| F5 | DEG | OK | active | OK | `degraded_suspected_capture` | Black/frozen capture (heuristic) | Capture reset before VM actions |

**UNR** = Thruk reports UNREACHABLE only (no DOWN/CRITICAL above threshold). **DOWN** = service/host DOWN or CRITICAL per policy thresholds.

**Degraded** external state uses configurable thresholds on CRITICAL/DOWN counts when not binary.

## Mapping to decision rules (Cases A–E)

| Case | Matrix rows | Notes |
| --- | --- | --- |
| A | F1 | Prefer low-impact OBS recovery, then start stream |
| B | F2 | External DOWN disagrees with OBS; capture reset → stop/start stream |
| F | F6 | UNREACHABLE + stream inactive → start stream (like F1, no capture reset) |
| G | F7 | UNREACHABLE + stream active → recheck monitoring only |
| C | F3 | Retry websocket, then restart OBS process via control API; do not use stop/start stream scripts |
| D | F4 | unRAID SSH + `virsh` behind explicit adapter |
| E | F5 | `OBS_Capture_Devices_Reset` analog before VM-level |
