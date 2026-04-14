# Recovery policy notes (MVP defaults)

## Remediation ladder (least disruptive first)

1. Re-check signals (confirm not flapping).
2. Retry websocket connection (transient network).
3. Query OBS state and stats for evidence.
4. Low-impact OBS actions (e.g. start stream via websocket).
5. **`run_capture_devices_reset()`** when capture/source failure is suspected.
6. **Controlled stream restart**: `run_stop_stream_script()` → wait → `run_start_stream_script()` and/or OBS API equivalents per config.
7. Future: Windows-side hook (placeholder interface).
8. **`restart_obs_vm()`** via SSH to unRAID + `virsh` — only after cooldowns and max lower-impact attempts.
9. Escalate to operator with full structured log.

## Cooldowns and limits

- Each action type has its own cooldown (seconds).
- Global **max actions per incident** (single orchestrator run) defaults conservatively.
- **Maintenance mode** disables all mutating remediations.

## Audit

Every action logs: timestamp, incident id, class, chosen action, dry-run flag, duration, exit code, truncated stdout/stderr.

## Configuration

Use `configs/config.example.yaml` as a template; copy to `configs/local.yaml` (gitignored) for secrets and hostnames.
