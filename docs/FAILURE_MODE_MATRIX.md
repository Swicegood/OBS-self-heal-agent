# Failure-mode matrix (MVP)

Legend: **Ext** = external/public (Thruk aggregate), **WS** = obs-websocket reachable, **Str** = OBS reports streaming active, **VM** = OBS VM network healthy (ping/TCP as configured).

| ID | Ext | WS | Str | VM | Class | Typical cause | First remediation (ladder) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1 | OK | OK | expected | OK | `healthy` | Nominal | None |
| F1 | DOWN | OK | inactive | OK | `public_down_obs_reachable_stream_inactive` | Output not started, profile issue | Confirm → OBS stream start (API or script) |
| F2 | DOWN | OK | active | OK | `public_down_obs_reachable_stream_active` | Stuck path, capture wedged, CDN/downstream | Evidence → capture reset → controlled stream restart |
| F3 | DOWN | NO | * | OK | `obs_websocket_unreachable_vm_reachable` | OBS wedge, WS off, firewall | Retry WS → script-based stream toggles if reachable → escalate toward process/VM |
| F4 | DOWN | NO | * | BAD | `vm_or_network_unhealthy` | VM/network/host issue | Infrastructure probes → SSH/virsh per policy |
| F5 | DEG | OK | active | OK | `degraded_suspected_capture` | Black/frozen capture (heuristic) | Capture reset before VM actions |

**Degraded** external state uses configurable thresholds on CRITICAL/DOWN counts when not binary.

## Mapping to decision rules (Cases A–E)

| Case | Matrix rows | Notes |
| --- | --- | --- |
| A | F1 | Prefer low-impact OBS recovery, then start stream |
| B | F2 | External disagrees with OBS; capture reset → stop/start stream |
| C | F3 | Avoid immediate VM reboot; try script/API paths that do not require stable WS if policy allows |
| D | F4 | unRAID SSH + `virsh` behind explicit adapter |
| E | F5 | `OBS_Capture_Devices_Reset` analog before VM-level |
