#!/usr/bin/env bash
# Run from crontab: probe first; on failure either run OPENCLAW_ON_PROBE_FAIL or invoke OpenClaw agent
# with the named job payload from OPENCLAW_JOBS_JSON (skill line + /skill obs-self-heal).
#
#   * * * * * OBS_SELF_HEAL_CONFIG="/home/jaga/myopenclaw/configs/local.yaml" OPENCLAW_CRON_JOB_NAME="OBS Check and Heal" OPENCLAW_REPLY_CHANNEL="telegram" OPENCLAW_REPLY_TO="8270383511" OPENCLAW_ON_PROBE_FAIL='/home/jaga/myopenclaw/.venv/bin/obs-self-heal run --config /home/jaga/myopenclaw/configs/local.yaml' "/home/jaga/myopenclaw/skills/obs-self-heal/scripts/cron-on-probe-fail.sh" >>"/tmp/obs-self-heal-probe-cron.log" 2>&1
#
# Failure path (first match wins):
#   1) OPENCLAW_ON_PROBE_FAIL — arbitrary shell command (e.g. direct `obs-self-heal run`).
#   2) Else OPENCLAW_CRON_JOB_NAME — load payload.text from OPENCLAW_JOBS_JSON and run:
#        openclaw agent --message $'/skill obs-self-heal\n\n'"$payload" [--deliver ...]
#
# Optional lock (avoid overlapping agent runs): OBS_SELF_HEAL_PROBE_FAIL_LOCK=/tmp/obs-self-heal-probe-fail.lock
#
set -euo pipefail

BIN="${OBS_SELF_HEAL_BIN:-obs-self-heal}"
CONFIG="${OBS_SELF_HEAL_CONFIG:-}"

OPENCLAW_BIN="${OPENCLAW_BIN:-$HOME/.nvm/versions/node/v22.22.2/bin/openclaw}"
OPENCLAW_NODE_BIN_DIR="${OPENCLAW_NODE_BIN_DIR:-$HOME/.nvm/versions/node/v22.22.2/bin}"
OPENCLAW_CRON_JOB_NAME="${OPENCLAW_CRON_JOB_NAME:-}"
OPENCLAW_JOBS_JSON="${OPENCLAW_JOBS_JSON:-$HOME/.openclaw/cron/jobs.json}"
OPENCLAW_AGENT_ID="${OPENCLAW_AGENT_ID:-main}"
OPENCLAW_REPLY_CHANNEL="${OPENCLAW_REPLY_CHANNEL:-telegram}"
OPENCLAW_REPLY_TO="${OPENCLAW_REPLY_TO:-}"

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

_load_obs_control_api_token() {
  # Avoid putting secrets directly in crontab env. If OBS_CONTROL_API_TOKEN is not already set,
  # read it from the repo config JSON (git-tracked by you).
  if [[ -n "${OBS_CONTROL_API_TOKEN:-}" ]]; then
    return 0
  fi

  local config_dir repo token_path
  config_dir="$(cd "$(dirname "$CONFIG")" && pwd)"
  repo="$(dirname "$config_dir")"
  token_path="${OBS_CONTROL_API_TOKEN_JSON:-$repo/configs/config.obs_api.json}"

  if [[ ! -f "$token_path" ]]; then
    return 0
  fi

  # No token printing: just export into this process environment.
  OBS_CONTROL_API_TOKEN="$(
    python3 - "$token_path" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
sys.stdout.write(str(data.get("api_token", "") or ""))
PY
  )" || OBS_CONTROL_API_TOKEN=""

  export OBS_CONTROL_API_TOKEN
}

_run_openclaw_from_cron_payload() {
  local job_name="$1"

  if [[ -z "$job_name" ]]; then
    return 2
  fi

  local oc="$OPENCLAW_BIN"
  if [[ "$oc" != */* ]]; then
    if ! command -v "$oc" >/dev/null 2>&1; then
      echo "cron-on-probe-fail: openclaw not found (set OPENCLAW_BIN or add openclaw to PATH)" >&2
      return 2
    fi
  else
    if [[ ! -x "$oc" ]]; then
      echo "cron-on-probe-fail: OPENCLAW_BIN not executable: $oc" >&2
      return 2
    fi
  fi

  if [[ ! -f "$OPENCLAW_JOBS_JSON" ]]; then
    echo "cron-on-probe-fail: missing OpenClaw cron store: $OPENCLAW_JOBS_JSON" >&2
    return 2
  fi

  local payload
  payload="$(
    python3 - "$OPENCLAW_JOBS_JSON" "$job_name" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
for j in data.get("jobs", []):
  if j.get("name") == name:
    p = j.get("payload", {})
    txt = p.get("text", "")
    sys.stdout.write(txt)
    raise SystemExit(0)
raise SystemExit(3)
PY
  )" || {
    rc=$?
    if [[ $rc -eq 3 ]]; then
      echo "cron-on-probe-fail: OpenClaw cron job not found by name: ${job_name}" >&2
    else
      echo "cron-on-probe-fail: failed to read OpenClaw cron payload (rc=$rc)" >&2
    fi
    return 2
  }

  # Prefix triggers skill activation in OpenClaw; payload holds the scheduled instructions.
  if [[ -n "$OPENCLAW_REPLY_TO" ]]; then
    PATH="$OPENCLAW_NODE_BIN_DIR:$PATH" "$oc" agent \
      --agent "$OPENCLAW_AGENT_ID" \
      --message $'/skill obs-self-heal\n\n'"$payload" \
      --deliver --reply-channel "$OPENCLAW_REPLY_CHANNEL" --reply-to "$OPENCLAW_REPLY_TO"
  else
    echo "cron-on-probe-fail: OPENCLAW_REPLY_TO unset; agent reply will not be pushed to Telegram" >&2
    PATH="$OPENCLAW_NODE_BIN_DIR:$PATH" "$oc" agent \
      --agent "$OPENCLAW_AGENT_ID" \
      --message $'/skill obs-self-heal\n\n'"$payload"
  fi
}

if "$BIN" probe --config "$CONFIG"; then
  exit 0
fi

if [[ -n "${OPENCLAW_ON_PROBE_FAIL:-}" || -n "${OPENCLAW_CRON_JOB_NAME:-}" ]]; then
  LOCK="${OBS_SELF_HEAL_PROBE_FAIL_LOCK:-/tmp/obs-self-heal-probe-fail.lock}"
  exec 200>"$LOCK"
  if ! flock -n 200; then
    echo "cron-on-probe-fail: remediation already in progress (lock $LOCK), skipping" >&2
    exit 0
  fi
fi

_load_obs_control_api_token

if [[ -n "${OPENCLAW_ON_PROBE_FAIL:-}" ]]; then
  bash -c "$OPENCLAW_ON_PROBE_FAIL"
elif [[ -n "$OPENCLAW_CRON_JOB_NAME" ]]; then
  _run_openclaw_from_cron_payload "$OPENCLAW_CRON_JOB_NAME" || true
fi

exit 0
