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
# Uplink gate (configs/local.yaml → uplink.*): when require_primary_egress is true, skip the entire
# heal path (no probe-fail agent, no Telegram) unless public egress IP matches primary_egress_ips/regex.
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
OPENCLAW_THROTTLE_SEC="${OPENCLAW_THROTTLE_SEC:-0}"
OPENCLAW_THROTTLE_STAMP="${OPENCLAW_THROTTLE_STAMP:-/tmp/obs-self-heal-openclaw-last-run.ts}"
OPENCLAW_NOTIFY_TOKENS_TO_TELEGRAM="${OPENCLAW_NOTIFY_TOKENS_TO_TELEGRAM:-1}"

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

# Returns 0 if heal is allowed, 1 if we should skip (backup WAN / check failed).
_uplink_allows_heal() {
  # Env overrides (optional): UPLINK_REQUIRE_PRIMARY, UPLINK_PRIMARY_EGRESS_IPS (comma-separated),
  # UPLINK_PRIMARY_EGRESS_REGEX, UPLINK_CHECK_URL, UPLINK_TIMEOUT_SEC, UPLINK_SKIP_ON_CHECK_FAILURE.
  local py config_dir repo
  config_dir="$(cd "$(dirname "$CONFIG")" && pwd)"
  repo="$(dirname "$config_dir")"
  py="${repo}/.venv/bin/python"
  if [[ ! -x "$py" ]]; then
    py="python3"
  fi

  "$py" - "$CONFIG" <<'PY'
import ipaddress
import os
import re
import sys
import urllib.request

import yaml

config_path = sys.argv[1]
raw = yaml.safe_load(open(config_path, encoding="utf-8")) or {}
up = raw.get("uplink") or {}

def env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")

require = env_bool("UPLINK_REQUIRE_PRIMARY", bool(up.get("require_primary_egress", False)))
if not require:
    raise SystemExit(0)

ips_env = os.environ.get("UPLINK_PRIMARY_EGRESS_IPS", "").strip()
if ips_env:
    primary_ips = [p.strip() for p in ips_env.split(",") if p.strip()]
else:
    primary_ips = [str(x).strip() for x in (up.get("primary_egress_ips") or []) if str(x).strip()]

regex = os.environ.get("UPLINK_PRIMARY_EGRESS_REGEX", "").strip() or str(up.get("primary_egress_regex") or "").strip()
check_url = os.environ.get("UPLINK_CHECK_URL", "").strip() or str(up.get("check_url") or "https://ifconfig.me/ip")
try:
    timeout = float(os.environ.get("UPLINK_TIMEOUT_SEC") or up.get("timeout_sec") or 3.0)
except (TypeError, ValueError):
    timeout = 3.0
skip_on_fail = env_bool("UPLINK_SKIP_ON_CHECK_FAILURE", bool(up.get("skip_on_check_failure", True)))

if not primary_ips and not regex:
    print(
        "cron-on-probe-fail: uplink.require_primary_egress set but no primary_egress_ips/regex; allowing heal",
        file=sys.stderr,
    )
    raise SystemExit(0)

try:
    req = urllib.request.Request(check_url, headers={"User-Agent": "obs-self-heal-uplink-check/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace").strip()
except Exception as e:  # noqa: BLE001
    print(
        f"cron-on-probe-fail: uplink check-ip failed ({type(e).__name__}: {e}); skip_heal={skip_on_fail}",
        file=sys.stderr,
    )
    raise SystemExit(1 if skip_on_fail else 0)

egress = ""
for tok in re.split(r"[\s,;]+", body):
    tok = tok.strip()
    try:
        ipaddress.ip_address(tok)
        egress = tok
        break
    except ValueError:
        continue

if not egress:
    print(
        f"cron-on-probe-fail: uplink check-ip returned no IP ({body[:80]!r}); skip_heal={skip_on_fail}",
        file=sys.stderr,
    )
    raise SystemExit(1 if skip_on_fail else 0)

ok = egress in primary_ips
if not ok and regex:
    try:
        ok = bool(re.fullmatch(regex, egress))
    except re.error as e:
        print(f"cron-on-probe-fail: invalid primary_egress_regex: {e}", file=sys.stderr)
        raise SystemExit(1 if skip_on_fail else 0)

if ok:
    print(f"cron-on-probe-fail: uplink primary ok egress={egress}", file=sys.stderr)
    raise SystemExit(0)

print(
    f"cron-on-probe-fail: skipping heal (egress={egress} not primary; backup WAN or unexpected IP)",
    file=sys.stderr,
)
raise SystemExit(1)
PY
}

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

  if [[ "$OPENCLAW_NOTIFY_TOKENS_TO_TELEGRAM" == "1" && -n "$OPENCLAW_REPLY_TO" ]]; then
    # This is a cheap, non-LLM message so we can see token-spending events even if the agent
    # delivery fails or produces no reply.
    PATH="$OPENCLAW_NODE_BIN_DIR:$PATH" "$oc" message send \
      --channel telegram \
      --target "$OPENCLAW_REPLY_TO" \
      --message "obs-self-heal: probe failed → invoking OpenClaw agent (${OPENCLAW_AGENT_ID}) for job '${job_name}'" \
      >/dev/null 2>&1 || true
  fi

  # Throttle repeated agent invocations while the probe stays failing (prevents invisible token burn).
  # This only gates the OpenClaw path; the probe itself still runs every cron tick.
  if [[ -n "$OPENCLAW_THROTTLE_SEC" && "$OPENCLAW_THROTTLE_SEC" != "0" ]]; then
    now="$(date +%s)"
    last="0"
    if [[ -f "$OPENCLAW_THROTTLE_STAMP" ]]; then
      last="$(cat "$OPENCLAW_THROTTLE_STAMP" 2>/dev/null || echo 0)"
    fi
    if [[ "$last" =~ ^[0-9]+$ ]] && (( now - last < OPENCLAW_THROTTLE_SEC )); then
      echo "cron-on-probe-fail: openclaw throttled (last=$last now=$now throttle_sec=$OPENCLAW_THROTTLE_SEC)" >&2
      return 0
    fi
  fi

  echo "$(date -Is) cron-on-probe-fail: invoking openclaw agent job='${job_name}' agent='${OPENCLAW_AGENT_ID}'" >&2
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

  # Record last successful attempt to invoke the agent (regardless of the agent's internal outcome).
  if [[ -n "$OPENCLAW_THROTTLE_SEC" && "$OPENCLAW_THROTTLE_SEC" != "0" ]]; then
    date +%s >"$OPENCLAW_THROTTLE_STAMP" 2>/dev/null || true
  fi
}

# Bail out before probe/agent/telegram when on backup WAN (or check-ip fails).
if ! _uplink_allows_heal; then
  exit 0
fi

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
