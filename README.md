# obs-self-heal

OpenClaw-centered **self-healing OBS** toolkit: health signals, policy, remediation adapters, verification, and structured audit logs. OpenClaw remains the orchestrator; this package is the operational library and CLI.

## Quick start

```bash
cd /home/jaga/myopenclaw
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp configs/config.example.yaml configs/local.yaml
# Edit configs/local.yaml — keep secrets out of git
export OBS_WEBSOCKET_PASSWORD='your-secret'
obs-self-heal run --config configs/local.yaml --dry-run
obs-self-heal run --config configs/local.yaml
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/FAILURE_MODE_MATRIX.md](docs/FAILURE_MODE_MATRIX.md)
- [docs/RECOVERY_POLICY.md](docs/RECOVERY_POLICY.md)

## OpenClaw integration

See [skills/README.md](skills/README.md) for wiring this CLI into OpenClaw skills and schedules.
