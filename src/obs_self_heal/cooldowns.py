from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from obs_self_heal.logging_setup import get_logger

LOG = get_logger("cooldowns")


class CooldownStore:
    """Persistent last-run timestamps per action key."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): float(v) for k, v in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
            LOG.warning("cooldown_load_failed", path=str(self.path), error=str(e))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def allowed(self, key: str, cooldown_sec: float, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        last = self._data.get(key)
        if last is None:
            return True
        return (t - last) >= cooldown_sec

    def touch(self, key: str, now: float | None = None) -> None:
        self._data[key] = now if now is not None else time.time()
        self._save()

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)
