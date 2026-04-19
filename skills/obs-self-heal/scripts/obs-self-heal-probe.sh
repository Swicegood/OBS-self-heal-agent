#!/usr/bin/env bash
set -euo pipefail

# Wrapper intended to be invoked from an OpenClaw skill tool.
# Exit code mirrors obs-self-heal probe:
# - 0 when monitoring looks healthy
# - 1 otherwise

BIN="${OBS_SELF_HEAL_BIN:-obs-self-heal}"

CONFIG="${OBS_SELF_HEAL_CONFIG:-}"
if [[ "$BIN" != */* ]] && ! command -v "$BIN" >/dev/null 2>&1 && [[ -n "$CONFIG" ]]; then
  config_dir="$(cd "$(dirname "$CONFIG")" && pwd)"
  repo="$(dirname "$config_dir")"
  candidate="$repo/.venv/bin/obs-self-heal"
  if [[ -x "$candidate" ]]; then
    BIN="$candidate"
  fi
fi

if [[ "$BIN" != */* ]] && ! command -v "$BIN" >/dev/null 2>&1; then
  echo "obs-self-heal-probe: ${BIN} not on PATH (set OBS_SELF_HEAL_BIN or activate venv)" >&2
  exit 2
fi

args=()
if [[ -n "$CONFIG" ]]; then
  args+=(--config "$CONFIG")
fi

exec "$BIN" probe "${args[@]}" "$@"
