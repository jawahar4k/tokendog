# src/tokendog/config.py
from __future__ import annotations
import os
from pathlib import Path

def tokendog_home() -> Path:
    root = os.environ.get("TOKENDOG_HOME")
    return Path(root).expanduser() if root else Path.home() / ".tokendog"

def telemetry_dir() -> Path:
    d = tokendog_home() / "telemetry"
    d.mkdir(parents=True, exist_ok=True)
    return d
