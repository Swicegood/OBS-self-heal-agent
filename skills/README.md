# OpenClaw skill wiring (obs-self-heal)

This repository implements the `**obs-self-heal**` Python package.

**Where OpenClaw “thinks”:** not inside the Python package. The **agent** (LLM + this skill’s instructions) decides *when* to run tools and *how* to interpret results; the **CLI** encodes fixed remediation policy in code. See [obs-self-heal/SKILL.md](obs-self-heal/SKILL.md) for the split between deterministic policy vs. agent reasoning.

OpenClaw remains the orchestrator: schedule or invoke the CLI from a skill, pass credential paths via environment, and forward structured logs to your usual log sink.

## Loading this skill from the Git repo (no symlink)

OpenClaw rejects workspace skill paths that symlink outside the workspace. Instead of copying or linking `skills/obs-self-heal` into `~/.openclaw/workspace/skills/`, add **`skills.load.extraDirs`** in `~/.openclaw/openclaw.json` pointing at this repository’s skill directory:

```json
"skills": {
  "load": {
    "extraDirs": [
      "/home/jaga/myopenclaw/skills/obs-self-heal"
    ]
  }
}
```

Restart the OpenClaw gateway after changing config. Prefer editing **`~/myopenclaw/skills/obs-self-heal`** as the single source of truth; remove duplicate `obs-self-heal` copies under `~/.openclaw/workspace/skills/` once you confirm the gateway lists the repo path (avoids two copies of the same skill).

## Cron (`cron-on-probe-fail.sh`) → OpenClaw agent

When `obs-self-heal probe` fails, the script can invoke **`openclaw agent`** with **`/skill obs-self-heal`** plus the **`payload.text`** for a named job in **`~/.openclaw/cron/jobs.json`**:

- Set **`OPENCLAW_CRON_JOB_NAME`** (e.g. `OBS Check and Heal`) on the cron line.
- Optionally set **`OPENCLAW_JOBS_JSON`**, **`OPENCLAW_AGENT_ID`**, **`OPENCLAW_REPLY_*`** for Telegram delivery.

If **`OPENCLAW_ON_PROBE_FAIL`** is set, it runs **instead** (direct remediation without the agent).

Example crontab line:

```bash
* * * * * OBS_SELF_HEAL_CONFIG="/home/jaga/myopenclaw/configs/local.yaml" OPENCLAW_CRON_JOB_NAME="OBS Check and Heal" OPENCLAW_REPLY_CHANNEL="telegram" OPENCLAW_REPLY_TO="8270383511" OPENCLAW_ON_PROBE_FAIL='/home/jaga/myopenclaw/.venv/bin/obs-self-heal run --config /home/jaga/myopenclaw/configs/local.yaml' "/home/jaga/myopenclaw/skills/obs-self-heal/scripts/cron-on-probe-fail.sh" >>"/tmp/obs-self-heal-probe-cron.log" 2>&1
```

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