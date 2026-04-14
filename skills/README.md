# OpenClaw skill wiring (obs-self-heal)

This repository implements the `**obs-self-heal**` Python package.

**Where OpenClaw “thinks”:** not inside the Python package. The **agent** (LLM + this skill’s instructions) decides *when* to run tools and *how* to interpret results; the **CLI** encodes fixed remediation policy in code. See [obs-self-heal/SKILL.md](obs-self-heal/SKILL.md) for the split between deterministic policy vs. agent reasoning.

OpenClaw remains the orchestrator: schedule or invoke the CLI from a skill, pass credential paths via environment, and forward structured logs to your usual log sink.

## Suggested invocation

From WSL2 (after `pip install -e .` or a venv):

```bash
export OBS_SELF_HEAL_CONFIG="$HOME/myopenclaw/configs/local.yaml"
export OBS_WEBSOCKET_PASSWORD="..."  # if using ${OBS_WEBSOCKET_PASSWORD} in YAML
obs-self-heal run
```

Use `--dry-run` until policy thresholds and script paths are validated.

## Local OpenAI-compatible model (OpenClaw)

This repo does not run an LLM directly; **OpenClaw** does. To make the OpenClaw agent use your local OpenAI-compatible server at `http://192.168.0.58:1234/v1`, configure OpenClaw to use:

- **Base URL**: `http://192.168.0.58:1234/v1`
- **API key**: whatever your server expects (many accept any non-empty string)
- **Model name**: whatever your server exposes (check via `GET /v1/models`)

If your OpenClaw setup honors OpenAI-style environment variables, the typical minimal wiring is:

```bash
export OPENAI_BASE_URL="http://192.168.0.58:1234/v1"
export OPENAI_API_KEY="local"
```

## Integrating `thruk_status.py`

The package wraps `~/.openclaw/workspace/skills/lan-monitoring/scripts/thruk_status.py` by path (see `configs/config.example.yaml`). Keep credentials in `~/.openclaw/credentials/monitoring-lan.json` or set `MONITORING_CREDS_FILE` in `thruk.env` in your local config.

## Operational scripts

Point `scripts.capture_devices_reset`, `scripts.start_stream`, and `scripts.stop_stream` at your real operational scripts (this repo ships `obs_reset_capture_devices.sh`, `obs_start_stream.sh`, `obs_stop_stream.sh` as examples). Do not commit secrets; prefer env substitution in YAML.