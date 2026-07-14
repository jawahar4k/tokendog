from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from .config import telemetry_dir
from .event import TokenEvent


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def write_event(event: TokenEvent) -> Path:
    path = telemetry_dir() / f"{_today()}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(event.to_json() + "\n")
    return path


def read_events() -> Iterator[TokenEvent]:
    for jf in sorted(telemetry_dir().glob("*.jsonl")):
        with jf.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield TokenEvent.from_json(line)
                    except (json.JSONDecodeError, TypeError, KeyError):
                        continue
