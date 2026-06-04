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
STATE_DIR="${OBS_RESET_STATE_DIR:-${TMPDIR:-/tmp}/obs-reset-capture-state}"
STATE_FILE="${OBS_RESET_STATE_FILE:-$STATE_DIR/state.json}"
mkdir -p "$STATE_DIR"
# Docker bind-mount creates a directory if the file path does not exist yet.
: >"$STATE_FILE"

# Set by caller (obs-self-heal merges scripts.env from config into subprocess env).
VM_HOST_SSH_HOST="${VM_HOST_SSH_HOST:-192.168.0.71}"
VM_HOST_SSH_PORT="${VM_HOST_SSH_PORT:-22}"
VM_HOST_SSH_USER="${VM_HOST_SSH_USER:-root}"

_PREPARE_DONE=0
restore_on_exit() {
  if [[ "$_PREPARE_DONE" -ne 1 ]] || [[ ! -s "$STATE_FILE" ]]; then
    return 0
  fi
  echo "trap: restoring shown capture items after interrupt/failure..." >&2
  obs_ws_phase restore || true
  if [[ -f "$STATE_FILE" ]]; then
    while IFS= read -r name; do
      [[ -z "$name" ]] && continue
      obs item show -s "$SCENE" "$name" || true
    done < <(python3 -c "
import json, sys
state = json.load(open(sys.argv[1]))
for it in state.get('items', []):
    if it.get('was_shown'):
        print(it['source_name'])
" "$STATE_FILE" 2>/dev/null) || true
  fi
}
trap restore_on_exit EXIT

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

# prepare: record which capture items were shown, then hide them (release device handles)
# restore: put back any item that was shown; verify enabled state; nudge video graph once
obs_ws_phase() {
  local phase="$1"
  docker run --rm -i \
    -v "${STATE_DIR}:/state:rw" \
    -e OBS_HOST="$OBS_HOST" \
    -e OBS_PORT="$OBS_PORT" \
    -e OBS_PASSWORD="$OBS_PASS" \
    -e OBS_SCENE="$SCENE" \
    -e OBS_VIDEO_ITEM="$VIDEO_ITEM" \
    -e OBS_AUDIO_ITEM="$AUDIO_ITEM" \
    -e OBS_WS_PHASE="$phase" \
    python:3.12-alpine sh -s <<'SH'
set -e
pip -q install obsws-python >/dev/null 2>&1
python - <<'PY'
import json
import os
import sys
import time
from pathlib import Path

from obsws_python import ReqClient

HOST = os.environ["OBS_HOST"]
PORT = int(os.environ["OBS_PORT"])
PASSWORD = os.environ["OBS_PASSWORD"]
SCENE = os.environ["OBS_SCENE"]
VIDEO = os.environ["OBS_VIDEO_ITEM"]
AUDIO = os.environ["OBS_AUDIO_ITEM"]
PHASE = os.environ["OBS_WS_PHASE"]
STATE_PATH = Path("/state/state.json")
CAPTURE_NAMES = (VIDEO, AUDIO)
FLIP_KEY = "flip_vertically"


def _item_dict(raw):
    if isinstance(raw, dict):
        return raw
    return getattr(raw, "__dict__", {}) or {}


def iter_scene_items(cl, scene_name: str):
    listed = cl.get_scene_item_list(scene_name)
    for raw in listed.scene_items or []:
        it = _item_dict(raw)
        if it.get("isGroup"):
            group_name = it["sourceName"]
            grouped = cl.get_group_scene_item_list(group_name)
            for grp_raw in grouped.scene_items or []:
                grp = _item_dict(grp_raw)
                grp["parentGroupName"] = group_name
                yield group_name, grp
        else:
            yield scene_name, it


def find_scene_item(cl, scene_name: str, source_name: str):
    for parent, it in iter_scene_items(cl, scene_name):
        if it.get("sourceName") == source_name:
            return parent, int(it["sceneItemId"])
    raise KeyError(f"scene item not found: {source_name!r} in scene {scene_name!r}")


def scene_item_enabled(cl, parent: str, item_id: int) -> bool:
    return bool(cl.get_scene_item_enabled(parent, item_id).scene_item_enabled)


def set_scene_item_enabled(cl, parent: str, item_id: int, enabled: bool) -> None:
    cl.set_scene_item_enabled(parent, item_id, enabled)


def ensure_parent_group_enabled(cl, scene_name: str, parent: str) -> None:
    if parent == scene_name:
        return
    listed = cl.get_scene_item_list(scene_name)
    for raw in listed.scene_items or []:
        it = _item_dict(raw)
        if it.get("isGroup") and it.get("sourceName") == parent:
            gid = int(it["sceneItemId"])
            if not scene_item_enabled(cl, scene_name, gid):
                set_scene_item_enabled(cl, scene_name, gid, True)
            return


def snapshot_item(cl, scene_name: str, source_name: str) -> dict:
    parent, item_id = find_scene_item(cl, scene_name, source_name)
    was_shown = scene_item_enabled(cl, parent, item_id)
    return {
        "source_name": source_name,
        "parent": parent,
        "scene_item_id": item_id,
        "was_shown": was_shown,
    }


def hide_item(cl, entry: dict) -> None:
    if not entry["was_shown"]:
        return
    set_scene_item_enabled(cl, entry["parent"], entry["scene_item_id"], False)


def show_item_with_retries(
    cl,
    scene_name: str,
    entry: dict,
    *,
    retries: int = 8,
    retry_delay: float = 2.5,
) -> None:
    if not entry["was_shown"]:
        return
    parent = entry["parent"]
    item_id = entry["scene_item_id"]
    name = entry["source_name"]
    ensure_parent_group_enabled(cl, scene_name, parent)
    for attempt in range(1, retries + 1):
        set_scene_item_enabled(cl, parent, item_id, True)
        time.sleep(retry_delay)
        if scene_item_enabled(cl, parent, item_id):
            print(f"restore: {name!r} is shown (attempt {attempt})")
            return
        print(
            f"WARN: {name!r} still hidden after show attempt {attempt}/{retries}",
            file=sys.stderr,
        )
    print(f"ERROR: {name!r} still hidden after restore", file=sys.stderr)
    sys.exit(1)


def nudge_capture_graph(cl, input_name: str) -> None:
    cur = cl.get_input_settings(input_name)
    settings = cur.input_settings or {}
    orig = bool(settings.get(FLIP_KEY, False))
    cl.set_input_settings(input_name, {FLIP_KEY: (not orig)}, overlay=True)
    time.sleep(0.6)
    cl.set_input_settings(input_name, {FLIP_KEY: orig}, overlay=True)
    time.sleep(0.2)


def main() -> None:
    cl = ReqClient(host=HOST, port=PORT, password=PASSWORD)
    if PHASE == "prepare":
        state = {
            "scene": SCENE,
            "items": [snapshot_item(cl, SCENE, name) for name in CAPTURE_NAMES],
        }
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        for entry in state["items"]:
            hide_item(cl, entry)
        print(json.dumps(state, indent=2))
        return

    if PHASE == "restore":
        if not STATE_PATH.is_file():
            print("ERROR: missing state file; run prepare phase first", file=sys.stderr)
            sys.exit(1)
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        for entry in state["items"]:
            show_item_with_retries(cl, state["scene"], entry)
        nudge_capture_graph(cl, VIDEO)
        for entry in state["items"]:
            if entry["was_shown"] and not scene_item_enabled(
                cl, entry["parent"], entry["scene_item_id"]
            ):
                print(
                    f"WARN: {entry['source_name']!r} hidden after graph nudge; re-showing",
                    file=sys.stderr,
                )
                show_item_with_retries(cl, state["scene"], entry, retries=4, retry_delay=1.0)
        return

    print(f"ERROR: unknown OBS_WS_PHASE={PHASE!r}", file=sys.stderr)
    sys.exit(1)


main()
PY
SH
}

echo "1) Snapshot which capture items are shown, then hide them (release handles)..."
echo "   VM host SSH: ${VM_HOST_SSH_USER}@${VM_HOST_SSH_HOST}:${VM_HOST_SSH_PORT}"
: >"$STATE_FILE"
obs_ws_phase prepare
_PREPARE_DONE=1

sleep 1

echo "2) Reset PCI USB controller ($PCI_DEV)..."
reset_pci_dev "$PCI_DEV"

# Let Windows fully re-enumerate the USB bus + DirectShow device.
sleep 18

echo "3) Restore shown state and nudge capture graph..."
obs_ws_phase restore

echo "4) obs-cli fallback show for items that were shown before reset..."
if [[ -f "$STATE_FILE" ]]; then
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    obs item show -s "$SCENE" "$name"
  done < <(python3 -c "
import json, sys
state = json.load(open(sys.argv[1]))
for it in state.get('items', []):
    if it.get('was_shown'):
        print(it['source_name'])
" "$STATE_FILE")
fi

_PREPARE_DONE=0
trap - EXIT
echo "Done."
