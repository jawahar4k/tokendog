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
