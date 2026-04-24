#!/bin/bash
set -euo pipefail

OBS_HOST="192.168.0.35"
OBS_PORT="4455"
CONFIG_PATH="${OBS_SELF_HEAL_CONFIG:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/configs/local.yaml"}"

get_obs_password() {
  local in_obs=0 line
  while IFS= read -r line; do
    if [[ "$line" =~ ^obs:[[:space:]]*$ ]]; then
      in_obs=1
      continue
    fi
    if [[ $in_obs -eq 1 && "$line" =~ ^[^[:space:]] ]]; then
      in_obs=0
    fi
    if [[ $in_obs -eq 1 && "$line" =~ ^[[:space:]]*password:[[:space:]]* ]]; then
      line="${line#*:}"
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%$'\r'}"
      line="${line%\"}"
      line="${line#\"}"
      line="${line%\'}"
      line="${line#\'}"
      printf '%s' "$line"
      return 0
    fi
  done < "$CONFIG_PATH"

  echo "ERROR: obs.password not found in $CONFIG_PATH" >&2
  return 1
}

OBS_PASS="$(get_obs_password)"

SCENE="Main"
VIDEO_ITEM="SDI Capture Device"
AUDIO_ITEM="Audio Input Capture 2"
PCI_DEV="0000:07:00.0"

VM_HOST_SSH_HOST="${VM_HOST_SSH_HOST:-"192.168.0.71"}"
VM_HOST_SSH_PORT="${VM_HOST_SSH_PORT:-"22"}"
VM_HOST_SSH_USER="${VM_HOST_SSH_USER:-"root"}"

vm_host() {
  ssh -p "$VM_HOST_SSH_PORT" "${VM_HOST_SSH_USER}@${VM_HOST_SSH_HOST}" "$@"
}

reset_pci_dev() {
  local dev="$1"
  vm_host env PCI_DEV="$dev" bash -s <<'SH'
set -euo pipefail
dev="${PCI_DEV:?missing PCI_DEV}"
sys="/sys/bus/pci/devices/$dev"
if [ ! -d "$sys" ]; then
  echo "ERROR: PCI device not found: $dev" >&2
  exit 1
fi

# Preferred: function-level reset
if [ -w "$sys/reset" ]; then
  if echo 1 > "$sys/reset" 2>/dev/null; then
    exit 0
  fi
  echo "WARN: write to $sys/reset failed; attempting fallbacks" >&2
else
  echo "WARN: $sys/reset not writable; attempting fallbacks" >&2
fi

# Fallback: remove + rescan (more disruptive, but forces re-enumeration)
if [ -w "$sys/remove" ] && [ -w /sys/bus/pci/rescan ]; then
  echo "INFO: pci remove+rescan for $dev" >&2
  echo 1 > "$sys/remove"
  sleep 1
  echo 1 > /sys/bus/pci/rescan
  exit 0
fi

echo "ERROR: unable to reset PCI device $dev (reset/remove-rescan unavailable/failed)" >&2
exit 1
SH
}

obs() {
  docker run --rm jagadguru/obs-cli:latest \
    -H "$OBS_HOST" -P "$OBS_PORT" -p "$OBS_PASS" "$@"
}

echo "1) Hide items (release handles)..."
obs item hide -s "$SCENE" "$VIDEO_ITEM"
obs item hide -s "$SCENE" "$AUDIO_ITEM" || true

sleep 1

echo "2) Reset PCI USB controller ($PCI_DEV)..."
reset_pci_dev "$PCI_DEV"

# IMPORTANT: let Windows fully re-enumerate the USB bus + DirectShow device
# This is the main difference from the earlier integrated attempt.
sleep 18

echo "3) Show SDI item (build a fresh graph after device is back)..."
obs item show -s "$SCENE" "$VIDEO_ITEM"

# Give OBS a moment to instantiate the source
sleep 2

echo "4) Nudge graph rebuild via flip toggle (overlay=true)..."
docker run --rm -i \
  -e OBS_HOST="$OBS_HOST" \
  -e OBS_PORT="$OBS_PORT" \
  -e OBS_PASSWORD="$OBS_PASS" \
  python:3.12-alpine sh <<'SH'
set -e
pip -q install obsws-python >/dev/null 2>&1
python - <<'PY'
import time
import os
from obsws_python import ReqClient

HOST=os.environ["OBS_HOST"]
PORT=int(os.environ["OBS_PORT"])
PASSWORD=os.environ["OBS_PASSWORD"]
INPUT="SDI Capture Device"
KEY="flip_vertically"

cl = ReqClient(host=HOST, port=PORT, password=PASSWORD)
cur = cl.get_input_settings(INPUT)
s = cur.input_settings or {}
orig = bool(s.get(KEY, False))

cl.set_input_settings(INPUT, {KEY: (not orig)}, overlay=True)
time.sleep(0.6)
cl.set_input_settings(INPUT, {KEY: orig}, overlay=True)
time.sleep(0.2)
print("OK")
PY
SH

echo "5) Show/unhide audio item..."
obs item show -s "$SCENE" "$AUDIO_ITEM" || true

sleep 20

echo "4) Nudge graph rebuild via flip toggle (overlay=true)..."
docker run --rm -i \
  -e OBS_HOST="$OBS_HOST" \
  -e OBS_PORT="$OBS_PORT" \
  -e OBS_PASSWORD="$OBS_PASS" \
  python:3.12-alpine sh <<'SH'
set -e
pip -q install obsws-python >/dev/null 2>&1
python - <<'PY'
import time
import os
from obsws_python import ReqClient

HOST=os.environ["OBS_HOST"]
PORT=int(os.environ["OBS_PORT"])
PASSWORD=os.environ["OBS_PASSWORD"]
INPUT="SDI Capture Device"
KEY="flip_vertically"

cl = ReqClient(host=HOST, port=PORT, password=PASSWORD)
cur = cl.get_input_settings(INPUT)
s = cur.input_settings or {}
orig = bool(s.get(KEY, False))

cl.set_input_settings(INPUT, {KEY: (not orig)}, overlay=True)
time.sleep(0.6)
cl.set_input_settings(INPUT, {KEY: orig}, overlay=True)
time.sleep(0.2)
print("OK")
PY
SH

echo "Done."