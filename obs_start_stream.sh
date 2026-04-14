#!/bin/bash
set -euo pipefail

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

docker run --rm jagadguru/obs-cli:latest \
  --host 192.168.0.35 --port 4455 --password "$OBS_PASS" \
  stream start