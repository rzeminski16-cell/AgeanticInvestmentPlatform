"""What the driver writes down: every decision, every stop, every readout."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["Recorder"]


class Recorder:
    """One directory per run; an append-only event log beside the run's artefacts."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._events = self.root / "driver.jsonl"

    def event(self, kind: str, **detail: Any) -> None:
        line = {"at": datetime.now(UTC).isoformat(), "event": kind, **detail}
        with self._events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, default=str) + "\n")
        print(f"[{line['at']}] {kind} {json.dumps(detail, default=str)[:400]}", flush=True)

    def write_json(self, name: str, payload: Any) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(payload, indent=2, default=str, sort_keys=False) + "\n", encoding="utf-8"
        )
        return path

    def write_text(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path
