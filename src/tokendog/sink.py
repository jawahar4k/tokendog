from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from .config import telemetry_dir, tokendog_home
from .event import TokenEvent

# The sink is append-only and unattended, which makes it prone to two silent
# failures: growing without bound, and stopping without anyone noticing.
# Both are handled here rather than left to each caller, because the hooks
# that write to it deliberately swallow every exception (they must never
# crash a session) and would otherwise hide a broken sink forever.

DEFAULT_MAX_SINK_MB = 64
DEFAULT_RETENTION_DAYS = 30

_pruned_this_process = False


def _int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name, "")).strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def max_sink_bytes() -> int:
    return _int_env("TOKENDOG_MAX_SINK_MB", DEFAULT_MAX_SINK_MB) * 1024 * 1024


def retention_days() -> int:
    return _int_env("TOKENDOG_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def health_path() -> Path:
    return tokendog_home() / "sink-health.json"


def sink_health() -> dict:
    """Current health of the sink. Never raises."""
    empty = {"degraded": False, "failures": 0, "sessions": 0,
             "sessions_capped": False, "first_ts": None, "last_ts": None,
             "last_reason": None}
    try:
        p = health_path()
        if not p.exists():
            return empty
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return empty
        sessions = data.get("sessions") or []
        failures = int(data.get("failures", 0) or 0)
        return {
            "degraded": failures > 0,
            "failures": failures,
            "sessions": len(sessions),
            "sessions_capped": bool(data.get("sessions_capped")),
            "first_ts": data.get("first_ts"),
            "last_ts": data.get("last_ts"),
            "last_reason": data.get("last_reason"),
        }
    except Exception:
        return empty


def clear_sink_health() -> None:
    """Forget past failures. Called on a successful write: the file answers
    'is the sink broken now?', not 'has it ever been broken?' — so a recovered
    sink stops warning instead of nagging forever."""
    try:
        p = health_path()
        if p.exists():
            p.unlink()
    except Exception:
        return


def record_failure(session_id: str | None, reason: str) -> None:
    """Record that an event could not be written. Never raises."""
    try:
        p = health_path()
        data = {}
        if p.exists():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        now = datetime.now(timezone.utc).isoformat()
        sessions = data.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
        sid = session_id or "unknown"
        capped = bool(data.get("sessions_capped"))
        if sid not in sessions:
            if len(sessions) < 50:
                sessions.append(sid)
            else:
                capped = True
        data = {
            "failures": int(data.get("failures", 0) or 0) + 1,
            "sessions": sessions,
            "sessions_capped": capped,
            "first_ts": data.get("first_ts") or now,
            "last_ts": now,
            "last_reason": str(reason)[:200],
        }
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        return


def prune_old_files(force: bool = False) -> int:
    """Delete daily sink files older than the retention window.

    Runs at most once per process so the common path stays a single append.
    """
    global _pruned_this_process
    if _pruned_this_process and not force:
        return 0
    _pruned_this_process = True
    removed = 0
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days())).date()
        for jf in telemetry_dir().glob("*.jsonl"):
            try:
                day = datetime.strptime(jf.stem, "%Y-%m-%d").date()
            except ValueError:
                continue  # not a daily file; leave it alone
            if day < cutoff:
                try:
                    jf.unlink()
                    removed += 1
                except OSError:
                    continue
    except Exception:
        return removed
    return removed


def write_event(event: TokenEvent) -> Path | None:
    """Append one event. Returns the path, or None if it could not be written.

    A None return is recorded in the sink-health file so a sink that has
    stopped working becomes visible instead of silently producing confident
    numbers from stale data.
    """
    try:
        prune_old_files()
        path = telemetry_dir() / f"{_today()}.jsonl"
        cap = max_sink_bytes()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size >= cap:
            record_failure(getattr(event, "session_id", None),
                           f"size cap reached ({cap // (1024 * 1024)} MB) — "
                           f"raise TOKENDOG_MAX_SINK_MB or lower TOKENDOG_RETENTION_DAYS")
            return None
        with path.open("a", encoding="utf-8") as f:
            f.write(event.to_json() + "\n")
    except Exception as exc:  # disk full, permissions, read-only fs...
        record_failure(getattr(event, "session_id", None),
                       f"{type(exc).__name__}: {exc}")
        return None
    clear_sink_health()
    return path


def read_events() -> Iterator[TokenEvent]:
    try:
        files = sorted(telemetry_dir().glob("*.jsonl"))
    except Exception:
        return
    for jf in files:
        try:
            handle = jf.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield TokenEvent.from_json(line)
                    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                        continue
