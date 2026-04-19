#!/usr/bin/env bash
set -euo pipefail

# Wrapper intended to be invoked from an OpenClaw skill tool.
# Requirements:
# - obs-self-heal installed / on PATH, OBS_SELF_HEAL_BIN set, or config under `<repo>/configs/` with
#   `<repo>/.venv/bin/obs-self-heal` present
# - config provided via --config or OBS_SELF_HEAL_CONFIG

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
  echo "obs-self-heal-run: ${BIN} not on PATH (set OBS_SELF_HEAL_BIN or activate venv)" >&2
  exit 2
fi

DRY="${OBS_SELF_HEAL_DRY_RUN:-}"

args=()
if [[ -n "$CONFIG" ]]; then
  args+=(--config "$CONFIG")
fi
if [[ "$DRY" == "1" || "$DRY" == "true" ]]; then
  args+=(--dry-run)
fi

exec "$BIN" run "${args[@]}" "$@"
