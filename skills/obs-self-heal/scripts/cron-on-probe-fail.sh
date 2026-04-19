#!/usr/bin/env bash
# Example: run every minute from user crontab (no LLM). Only run your OpenClaw / expensive step when
# Thruk/OMD monitoring is unhealthy.
#
# Intervention ordering inside `obs-self-heal run`:
# 1) OBS WebSocket control (first resort)
# 2) OBS Control API on the Windows host (second resort; HTTP, see REMOTE_API_GUIDE.md)
# 3) SSH/virsh VM restart (last resort; policy-gated)
#
#   * * * * * OBS_SELF_HEAL_CONFIG=/home/you/myopenclaw/configs/local.yaml /path/to/cron-on-probe-fail.sh >>/tmp/obs-probe.log 2>&1
#
# Cron has a minimal PATH: install the package, activate the project venv, set OBS_SELF_HEAL_BIN to
# the entrypoint, or keep config under `<repo>/configs/` so this script can use `<repo>/.venv/bin/obs-self-heal`.
#
# Optional: command to run when the probe fails (OpenClaw hook, Agent URL, etc.):
#   export OPENCLAW_ON_PROBE_FAIL='curl -fsS -X POST https://...'
# Or run deterministic remediation with no GPT usage:
#   export OPENCLAW_ON_PROBE_FAIL='obs-self-heal run --config "$OBS_SELF_HEAL_CONFIG"'
#
set -euo pipefail

BIN="${OBS_SELF_HEAL_BIN:-obs-self-heal}"
CONFIG="${OBS_SELF_HEAL_CONFIG:-}"

if [[ -z "$CONFIG" ]]; then
  echo "cron-on-probe-fail: set OBS_SELF_HEAL_CONFIG to your local.yaml" >&2
  exit 2
fi

if [[ "$BIN" != */* ]] && ! command -v "$BIN" >/dev/null 2>&1; then
  config_dir="$(cd "$(dirname "$CONFIG")" && pwd)"
  repo="$(dirname "$config_dir")"
  candidate="$repo/.venv/bin/obs-self-heal"
  if [[ -x "$candidate" ]]; then
    BIN="$candidate"
  fi
fi

if [[ "$BIN" != */* ]] && ! command -v "$BIN" >/dev/null 2>&1; then
  echo "cron-on-probe-fail: ${BIN} not on PATH (set OBS_SELF_HEAL_BIN or activate venv)" >&2
  exit 2
fi
if [[ "$BIN" == */* ]] && [[ ! -x "$BIN" ]]; then
  echo "cron-on-probe-fail: not executable: $BIN" >&2
  exit 2
fi

if "$BIN" probe --config "$CONFIG"; then
  exit 0
fi

if [[ -n "${OPENCLAW_ON_PROBE_FAIL:-}" ]]; then
  bash -c "$OPENCLAW_ON_PROBE_FAIL"
fi

exit 0
