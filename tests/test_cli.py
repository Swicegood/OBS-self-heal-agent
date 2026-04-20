from pathlib import Path

from obs_self_heal.cli import main


def test_reset_cooldowns_removes_state_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                f'state_dir: "{tmp_path.as_posix()}"',
                "thruk:",
                '  script_path: "/tmp/thruk_status.py"',
                "  critical_threshold: 1",
                "  down_unhealthy: true",
                "obs:",
                '  host: "127.0.0.1"',
                "  port: 4455",
                '  password: ""',
                "scripts:",
                '  capture_devices_reset: "/tmp/cap.sh"',
                '  start_stream: "/tmp/start.sh"',
                '  stop_stream: "/tmp/stop.sh"',
                "reachability:",
                "  obs_vm:",
                '    host: "127.0.0.1"',
                "    ping_count: 0",
                "    tcp_ports: []",
                "unraid:",
                "  ssh:",
                '    host: "127.0.0.2"',
                "  vm:",
                '    name: "obs"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    cd = tmp_path / "cooldowns.json"
    cd.write_text('{"obs_start_stream": 1.0}', encoding="utf-8")

    rc = main(["reset-cooldowns", "--config", str(cfg_path)])
    assert rc == 0
    assert not cd.exists()
