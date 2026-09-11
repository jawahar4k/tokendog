from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from .config import tokendog_home
from .approx import approx_tokens

# The savings ledger answers "what did TokenDog actually do to my calls, and
# what would have happened without it?" It is a SEPARATE file from telemetry so
# it never affects cost roll-ups. Each record is one truncation, in one of two
# modes:
#   enforce — the tool output was actually shortened (the model saw `kept`).
#   shadow  — nothing was changed; we only recorded what WOULD have been saved.
# Run in shadow first to see the savings with zero risk, then switch to enforce.


def savings_path() -> Path:
    home = tokendog_home()
    home.mkdir(parents=True, exist_ok=True)
    return home / "savings.jsonl"


def record_savings(*, session_id: str | None, tool: str | None, original: str,
                   kept: str, mode: str, runtime: str = "claude-code") -> None:
    """Append one truncation record. Never raises (mirrors the hooks' fail-open rule)."""
    try:
        orig_tokens = approx_tokens(original)
        kept_tokens = approx_tokens(kept)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id or "unknown",
            "runtime": runtime,
            "tool": tool or "unknown",
            "mode": mode,
            "original_tokens": orig_tokens,
            "kept_tokens": kept_tokens,
            "saved_tokens": max(0, orig_tokens - kept_tokens),
            "original_bytes": len(original.encode("utf-8")),
            "kept_bytes": len(kept.encode("utf-8")),
        }
        with savings_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        _bump_statusline_cache(rec["session_id"], rec["saved_tokens"])
    except Exception:
        return


def _bump_statusline_cache(session_id: str, saved: int) -> None:
    """Keep a tiny per-session running total where the statusline can read it.

    The statusline runs under a bare `python3` that cannot import tokendog and
    must not scan the ledger on every keystroke, so — exactly like the baseline
    cache — a tokendog-capable writer (this function, on each recorded event)
    maintains a small JSON it just reads. Keyed by session so the line shows what
    the condenser bought THIS session; naturally absent (segment hidden) until
    the first event, which only happens when the condenser is switched on.
    """
    if saved <= 0:
        return
    try:
        path = tokendog_home() / "statusline_savings.json"
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cache, dict):
                cache = {}
        except (OSError, ValueError):
            cache = {}
        sessions = cache.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
        cur = sessions.get(session_id) or {"saved": 0, "events": 0}
        cur["saved"] = int(cur.get("saved", 0)) + int(saved)
        cur["events"] = int(cur.get("events", 0)) + 1
        # Re-insert last so the most-recent session is newest in order.
        sessions.pop(session_id, None)
        sessions[session_id] = cur
        if len(sessions) > 50:                     # bound the file
            for k in list(sessions)[:-50]:
                sessions.pop(k, None)
        cache["sessions"] = sessions
        path.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    except Exception:
        return


def read_savings() -> Iterator[dict]:
    p = savings_path()
    if not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue


def savings_summary() -> dict:
    events = total_saved = total_original = 0
    per_tool: dict[str, int] = {}
    per_session: dict[str, int] = {}
    modes: dict[str, int] = {}
    for r in read_savings():
        events += 1
        saved = int(r.get("saved_tokens", 0) or 0)
        original = int(r.get("original_tokens", 0) or 0)
        total_saved += saved
        total_original += original
        tool = r.get("tool") or "unknown"
        per_tool[tool] = per_tool.get(tool, 0) + saved
        session = r.get("session_id") or "unknown"
        per_session[session] = per_session.get(session, 0) + saved
        mode = r.get("mode") or "enforce"
        modes[mode] = modes.get(mode, 0) + 1
    pct = (total_saved / total_original * 100.0) if total_original else 0.0
    return {
        "events": events,
        "total_saved": total_saved,
        "total_original": total_original,
        "pct": pct,
        "per_tool": per_tool,
        "per_session": per_session,
        "modes": modes,
    }
