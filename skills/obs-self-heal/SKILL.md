---
name: obs-self-heal
description: Self-healing OBS stream operations for the Mayapur/unRAID environment. Use when the user asks about stream health, OBS recovery, Thruk showing the stream down, capture reset, virsh/VM recovery, or running the obs-self-heal toolkit from WSL2.
---

# OBS self-heal (OpenClaw integration)

## Where “thinking” happens (two layers)

| Layer | What does the reasoning? | When |
| --- | --- | --- |
| **A. Policy engine (this repo)** | Deterministic rules in Python (`classify_incident`, `choose_remediation`, cooldowns). Not an LLM. | `obs-self-heal run`, cron, or any direct CLI invocation. |
| **B. OpenClaw agent (you)** | The model reads this skill, user context, and **tool output** (JSON logs / CLI stdout) and decides explanations, escalation, extra checks, or “do not remediate now.” | When the user chats, or when you explicitly invoke the bash tool to run the CLI and then interpret results. |

OpenClaw does **not** embed inside `obs-self-heal`; it **calls** it as a tool and **interprets** the outcome. The repo’s “autonomous operator” behavior is the **Python remediation ladder**, not GPT.

## How you should use this skill (agent behavior)

1. Prefer **gathering signals** before destructive action: if the user only asks “is the stream ok?”, you may suggest or run a **dry-run** first.
2. To run the pipeline from WSL2 (project venv or installed package):

   ```bash
   export OBS_SELF_HEAL_CONFIG="/home/jaga/myopenclaw/configs/local.yaml"
   obs-self-heal run --dry-run
   # then if appropriate:
   obs-self-heal run
   ```

3. **Read the JSON** printed to stdout (classification, plan, execution, verify). Summarize for the user: incident class, action taken or skipped, whether verification showed improvement.
4. **LLM judgment** applies to: whether to run at all, whether to escalate to a human, whether maintenance mode might be set, and whether to combine with other skills (e.g. `lan-monitoring` / browser Thruk).

## Local OpenAI-compatible model (OpenClaw)

This skill assumes the **OpenClaw agent** can be configured to use an OpenAI-compatible endpoint.

- **Endpoint**: `http://192.168.0.58:1234/v1`
- **Note**: model IDs are server-specific; check `GET /v1/models` on that host to see available names.

If your OpenClaw runtime uses OpenAI-style env vars, set:

```bash
export OPENAI_BASE_URL="http://192.168.0.58:1234/v1"
export OPENAI_API_KEY="local"
```

### Thruk / public signal when `thruk.scope.delegate_public_to_openclaw` is true

The CLI still logs in and fetches the Thruk TAC page itself, but **does not** use aggregate keyword counts for policy. Instead the run JSON includes:

- `thruk_tac_html_for_agent` — HTML excerpt of the tactical page (size capped by `openclaw_tac_html_max_chars`)
- `signals_before.public.evaluation_delegated_to_openclaw` — `true`
- `evidence.public_evaluation_delegated` — `true`

**You (the agent)** should read that HTML and decide whether the monitored service (see `thruk.scope.service_substring` and `host_substrings` in config) is actually UP/DOWN for user-facing purposes. Automated remediation will **not** trigger `public_down_*` solely from Thruk in this mode; OBS/WebSocket rules still apply.

## Relation to `lan-monitoring`

- When scope + delegate are off, `thruk_status.py` remains the scripted Nagios/Thruk tactical check and `obs-self-heal` **wraps** it for automated policy.
- For interactive investigation, you may still use the **browser** path described in `lan-monitoring` if the user needs UI detail.

## Copy into OpenClaw workspace (optional)

To make this skill visible to the gateway, copy or symlink this folder next to your other skills, e.g.:

`~/.openclaw/workspace/skills/obs-self-heal/`

Then restart the OpenClaw gateway if your setup requires skill reload.
