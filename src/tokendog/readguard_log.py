from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import tokendog_home
from .readguard import DEFAULT_RATIO, Decision, _env_num

# What the guard would have done, so the decision can be judged on the reader's
# own history before it ever changes one. Shadow mode is worthless without this:
# a log nobody can add up is not evidence.
#
# Allows are recorded as well as suggestions. A guard that fires rarely and a
# guard that is broken look identical from a file containing only the times it
# fired.
#
# Numeric plus a file extension. Never a path: it leaks the home directory and
# often a client's name, which is the line every TokenDog surface holds.

_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

_SEEN: dict[str, set[str]] = {}


def log_path(home=None) -> Path:
    return (Path(home) if home is not None else tokendog_home()) / "readguard.jsonl"


def seen_paths(session_id) -> set[str]:
    """Paths already suggested in this session. A repeat is always allowed.

    In-process, deliberately: the hook is one short-lived process per tool call,
    so this only dedupes within a call today — but the set is threaded through
    `assess` so the rule lives in one place, and a future long-lived caller gets
    it for free. A bad session id still gets a usable set rather than an error.
    """
    key = session_id if isinstance(session_id, str) and _SESSION_ID.match(session_id) else "_"
    return _SEEN.setdefault(key, set())


def would_save(rec: dict) -> int:
    """Net tokens a suggestion would have saved, charged for its own cost.

    Carrying the file costs a cache write plus a re-read on each remaining turn
    (4·F in the model); the block costs one extra round trip that re-reads the
    window at cache-read rate (0.1·ctx). The saving is the difference, and it
    can be negative — which is exactly why the guard declines those.
    """
    ratio = _env_num("TOKENDOG_READGUARD_RATIO", DEFAULT_RATIO)
    keep = (ratio / 10.0) * int(rec.get("file_tokens") or 0)
    block = int(rec.get("context_tokens") or 0) / 10.0
    return int(keep - block)


def record(session_id, decision: Decision, *, mode: str, home=None) -> None:
    """Append one decision. Never raises — this runs inside a hook."""
    try:
        rec = decision.as_record()
        rec["ts"] = datetime.now(timezone.utc).isoformat()
        rec["mode"] = mode
        path = log_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        return


def _parse(ts):
    try:
        out = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def read_records(home=None):
    path = log_path(home)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            yield rec


def summary(days: int | None = None, home=None) -> dict:
    """Totals over the last `days` (all of it when None)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
    seen = suggested = saved = 0
    by_ext: dict[str, dict] = {}
    modes: dict[str, int] = {}
    for rec in read_records(home):
        when = _parse(rec.get("ts"))
        if cutoff and (when is None or when < cutoff):
            continue
        seen += 1
        modes[rec.get("mode") or "?"] = modes.get(rec.get("mode") or "?", 0) + 1
        ext = rec.get("ext") or "(none)"
        e = by_ext.setdefault(ext, {"seen": 0, "suggested": 0, "would_save": 0})
        e["seen"] += 1
        if rec.get("action") == "suggest":
            suggested += 1
            net = would_save(rec)
            saved += net
            e["suggested"] += 1
            e["would_save"] += net
    return {"seen": seen, "suggested": suggested, "would_save": saved,
            "modes": modes, "days": days,
            "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1]["would_save"]))}
