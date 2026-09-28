#!/usr/bin/env python3
"""TokenDog statusline — makes context economics visible while you work.

Claude Code pipes a JSON payload to this script on every interaction and renders
whatever it prints. See https://code.claude.com/docs/en/statusline

Why this exists: cost per call scales with how full the context window is, because
the whole prompt is re-billed every turn. That number was invisible during the
session that motivated this script — 30 calls at ~980K occupancy cost 29.5M tokens
to add 27K of content. The window occupancy and the 7-day limit are the two figures
that would have prevented it, so they go on screen permanently.

Thresholds are absolute token counts, not percentages of the window. A 1M window at
30% is 300K carried on every turn — expensive in absolute terms even though the
percentage looks calm. Measured on this machine's own history: turns at or above
200K carried 83.5% of all context tokens, which is where CTX_WARN comes from.

Every field is treated as optional: the payload shape varies by Claude Code version
and by whether a session has made an API call yet. A statusline that raises renders
as an error on every keystroke, so this one degrades to fewer segments instead.
"""
from __future__ import annotations

import json
import os
import re
import sys

# The brand mark, shown before the figures so the line is identifiable at a
# glance among other statusline output. Override with TOKENDOG_STATUSLINE_TAG
# ("TokenDog" for a text mark, empty to drop it) — some terminals render an
# emoji at double width or not at all, and that is the reader's call, not ours.
TAG = os.environ.get("TOKENDOG_STATUSLINE_TAG", "\U0001F415")

# --- thresholds ---------------------------------------------------------------
CTX_WARN = 200_000      # absolute occupancy where per-turn cost starts to bite
CTX_ALARM = 400_000     # past here a trivial tool call costs more than most replies
AGE_WARN_H = 8.0        # a session older than a workday has drifted from its task
AGE_ALARM_H = 24.0      # overnight-idle sessions resume at full occupancy
# Keyed on HISTORY, deliberately, not on how full the window looks. A percentage
# cannot tell a connector you never call from a transcript you could clear, and
# /clear re-injects setup — so a percentage rule nags at a 1M window that is
# fine and stays silent on a 120k one that is all dead schema.
WORK_ALARM = 400_000    # history this big is what /clear and /compact are for
SETUP_ALARM = 120_000   # re-sent every turn; only disabling something moves it
LIMIT_WARN = 60.0       # % of a rate-limit window consumed
LIMIT_ALARM = 85.0

# --- ansi ---------------------------------------------------------------------
DIM = "\033[2m"
RED = "\033[31m"
YEL = "\033[33m"
GRN = "\033[32m"
CYA = "\033[36m"
BLD = "\033[1m"
OFF = "\033[0m"


def paint(text: str, colour: str) -> str:
    return f"{colour}{text}{OFF}" if colour else text


def grade(value: float | None, warn: float, alarm: float) -> str:
    """Colour for a value where higher is worse. Unknown values stay uncoloured."""
    if value is None:
        return ""
    if value >= alarm:
        return RED
    if value >= warn:
        return YEL
    return GRN


def human(n: float | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    if n < 1_000:
        return f"{n:.0f}"
    if n < 1_000_000:
        return f"{n / 1_000:.0f}K"
    return f"{n / 1_000_000:.2f}M"


def window_label(size: int | None) -> str:
    if not size:
        return "?"
    return "1M" if size >= 1_000_000 else f"{size // 1000}K"


def dig(obj, *path, default=None):
    """Walk nested dict keys, returning `default` the moment anything is missing."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def num(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def occupancy(payload: dict) -> int | None:
    """Tokens carried into the current turn.

    Prefers the harness's own total; falls back to summing the per-turn usage
    parts. Output tokens are deliberately excluded — they came back, they were
    not carried, and including them would overstate what the next turn re-reads.
    """
    total = num(dig(payload, "context_window", "total_input_tokens"))
    if total:
        return int(total)
    usage = dig(payload, "context_window", "current_usage", default={})
    if isinstance(usage, dict):
        parts = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        summed = sum(num(usage.get(k)) or 0 for k in parts)
        if summed:
            return int(summed)
    return None


def floor_segment() -> str | None:
    """The always-on floor (MCP schemas + skills + instructions), by size.

    ON by default; set TOKENDOG_STATUSLINE_FLOOR=0 (or off/no) to hide it. It is
    the "baseline" — the tokens loaded into the window before you type anything:
    every enabled MCP's tool schemas, each skill's always-on header, the
    instruction files. A fixed, static cost paid on every turn. It is shown apart
    from the ctx figure on purpose: ctx is dominated by conversation history, and
    Claude Code's payload carries NO per-source breakdown of that, so the
    baseline is the one honest source split the statusline can show.

    Pure READ, no tokendog import: the statusline runs under whatever `python3`
    is first on PATH (often /usr/bin/python3, which cannot import tokendog), so
    it must not depend on the import. A tokendog-capable SessionStart hook writes
    the sizes to a small JSON cache; this just reads and renders it. If the cache
    is missing, the segment is dropped and the line still renders — the
    statusline's works-without-tokendog guarantee is preserved.
    """
    # On by default; only an explicit off-value hides it.
    if str(os.environ.get("TOKENDOG_STATUSLINE_FLOOR", "1")).strip().lower() in (
            "0", "false", "no", "off"):
        return None
    home = os.environ.get("TOKENDOG_HOME") or os.path.join(os.path.expanduser("~"), ".tokendog")
    cache_path = os.path.join(home, "statusline_floor.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            sizes = json.load(fh).get("sizes")
    except (OSError, ValueError):
        return None
    total = sizes.get("total") if isinstance(sizes, dict) else None
    if not total:
        return None

    # A reader-friendly token count: keep one decimal under 10K so 2,335 reads as
    # "2.3K", not "2K" — the floor numbers are small and the precision matters.
    def fk(n: int) -> str:
        n = int(n or 0)
        if n < 1_000:
            return str(n)
        if n < 10_000:
            return f"{n / 1_000:.1f}K"
        return human(n)

    # Show EVERY kind that is present, spelled out, so "floor 2.4K (MCP 2.3K ·
    # skills 66)" reads at a glance. The full itemised list is `tokendog floor`.
    NAMES = [("mcp", "MCP"), ("skill", "skills"), ("instruction", "instructions")]
    parts = [f"{label} {fk(sizes[key])}" for key, label in NAMES if sizes.get(key)]
    breakdown = f" ({' · '.join(parts)})" if parts else ""
    return paint(f"baseline {fk(total)}{breakdown}", DIM)


def read_split(payload: dict) -> dict | None:
    """This session's setup/work split, as the Stop hook last left it.

    Pure READ, no tokendog import: the statusline runs under whatever `python3`
    is first on PATH (often /usr/bin/python3, which cannot import tokendog), so
    it must not depend on the import. `split_refresh` writes the file at the end
    of each turn; this just reads it. Missing or corrupt → None, and the line
    renders without the segment.
    """
    session = payload.get("session_id")
    if not isinstance(session, str) or not re.match(r"^[A-Za-z0-9-]{8,64}$", session):
        return None
    home = os.environ.get("TOKENDOG_HOME") or os.path.join(os.path.expanduser("~"), ".tokendog")
    try:
        with open(os.path.join(home, "split", session + ".json"), encoding="utf-8") as fh:
            split = json.load(fh)["split"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return split if isinstance(split, dict) else None


def split_segment(payload: dict) -> str | None:
    """`setup 70K (sys 26K · mcp 31K · skills 13K) · work 95K`.

    The two halves answer different questions: setup shrinks only by disabling
    something and is re-injected by /clear, work shrinks only by /clear. Showing
    them apart is the whole point — a single percentage cannot say which.
    """
    if str(os.environ.get("TOKENDOG_STATUSLINE_SPLIT", "1")).strip().lower() in (
            "0", "false", "no", "off"):
        return None
    split = read_split(payload)
    if not split:
        return None
    setup, work = split.get("setup") or {}, split.get("work") or {}
    setup_total, work_total = int(split.get("setup_total") or 0), int(split.get("work_total") or 0)
    if not setup_total and not work_total:
        return None
    # Parts under this are noise on a one-line statusline: knowing the subagent
    # listing costs 300 tokens changes nothing anyone would do.
    NAMED = (("sys", "sys"), ("mcp", "mcp"), ("skills", "skills"), ("agents", "agents"))
    parts = [f"{label} {human(setup[key])}" for key, label in NAMED
             if int(setup.get(key) or 0) >= 500]
    text = f"setup {human(setup_total)}"
    if parts:
        text += " (" + " · ".join(parts) + ")"
    text += f" · work {human(work_total)}"
    return paint(text, grade(float(setup_total), SETUP_ALARM, SETUP_ALARM * 2)
                 if setup_total >= SETUP_ALARM else DIM)


def lever_hint(payload: dict) -> str | None:
    """One verb, aimed at the half that is actually heavy.

    History is what /clear and /compact remove. Setup survives them both, so a
    session carrying 300k of connector schema is told to look at the floor, not
    to throw away its working state for nothing.
    """
    split = read_split(payload)
    if not split:
        return None
    if int(split.get("work_total") or 0) >= WORK_ALARM:
        return "→ /clear or /compact"
    if int(split.get("setup_total") or 0) >= SETUP_ALARM:
        return "→ setup is heavy, /tokendog:floor"
    return None


def savings_segment(payload: dict) -> str | None:
    """What the condenser has saved THIS session, when it is switched on.

    ON by default; set TOKENDOG_STATUSLINE_SAVINGS=0 (or off/no) to hide it. Like
    the baseline, it is a pure cache READ (no tokendog import): a tokendog-capable
    writer records each condense event and keeps a small per-session total in
    `${TOKENDOG_HOME or ~/.tokendog}/statusline_savings.json`; this looks up the
    current session and renders it. Absent until the first event — which only
    happens once the condenser is in shadow/enforce mode — so the segment stays
    off, and off the line, for anyone who has not turned condensing on.

    This is the RECORDED saving (what actually happened), never the projection —
    the projection is a multi-second transcript scan and has no place on a line
    that redraws on every keystroke; it lives in the dashboard Savings tab.
    """
    if str(os.environ.get("TOKENDOG_STATUSLINE_SAVINGS", "1")).strip().lower() in (
            "0", "false", "no", "off"):
        return None
    sid = payload.get("session_id")
    if not sid:
        return None
    home = os.environ.get("TOKENDOG_HOME") or os.path.join(os.path.expanduser("~"), ".tokendog")
    try:
        with open(os.path.join(home, "statusline_savings.json"), encoding="utf-8") as fh:
            sessions = json.load(fh).get("sessions") or {}
    except (OSError, ValueError):
        return None
    entry = sessions.get(sid) if isinstance(sessions, dict) else None
    saved = (entry or {}).get("saved") if isinstance(entry, dict) else None
    if not saved:
        return None
    return paint(f"saved {human(saved)}", GRN)


def limit_segment(payload: dict, key: str, label: str) -> str | None:
    pct = num(dig(payload, "rate_limits", key, "used_percentage"))
    if pct is None:
        return None
    return paint(f"{label} {pct:.0f}%", grade(pct, LIMIT_WARN, LIMIT_ALARM))


def build(payload: dict) -> str:
    segments: list[str] = []

    model = dig(payload, "model", "display_name") or dig(payload, "model", "id")
    if model:
        label = str(model)
        if payload.get("fast_mode"):
            label += "⚡"
        segments.append(paint(label, BLD))

    # Context occupancy — the headline. Absolute tokens first, percentage second:
    # the absolute number is what multiplies into every subsequent call.
    used = occupancy(payload)
    size = num(dig(payload, "context_window", "context_window_size"))
    if used is not None:
        colour = grade(float(used), CTX_WARN, CTX_ALARM)
        text = f"ctx {human(used)}/{window_label(int(size) if size else None)}"
        pct = num(dig(payload, "context_window", "used_percentage"))
        if pct is not None:
            text += f" {pct:.0f}%"
        segments.append(paint(text, colour))

    # Session age, from the harness's own wall-clock duration.
    age_ms = num(dig(payload, "cost", "total_duration_ms"))
    if age_ms:
        hours = age_ms / 3_600_000
        if hours >= 1:
            segments.append(paint(f"age {hours:.1f}h", grade(hours, AGE_WARN_H, AGE_ALARM_H)))

    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        seg = limit_segment(payload, key, label)
        if seg:
            segments.append(seg)

    # Where the window actually went, by which lever moves it. Preferred over
    # the static floor segment when present: it is measured from this session's
    # own transcript rather than from what is installed on disk.
    spl = split_segment(payload)
    if spl:
        segments.append(spl)
    else:
        flr = floor_segment()
        if flr:
            segments.append(flr)

    # Condenser savings for this session — present only when condensing is on.
    sv = savings_segment(payload)
    if sv:
        segments.append(sv)

    spend = num(dig(payload, "cost", "total_cost_usd"))
    if spend:
        segments.append(paint(f"${spend:.2f}", DIM))

    # One actionable verb, and only when something is actually wrong — a nudge that
    # fires constantly is a nudge nobody reads.
    # The split knows which half is heavy, so it names the lever that works.
    # Occupancy is the fallback for a session with no split cached yet.
    hint = lever_hint(payload)
    if hint:
        pass
    elif used is not None and used >= CTX_ALARM:
        hint = "→ /clear"
    elif used is not None and used >= CTX_WARN:
        hint = "→ /compact"
    elif age_ms and age_ms / 3_600_000 >= AGE_ALARM_H:
        hint = "→ stale, /clear"
    if hint:
        segments.append(paint(hint, RED if "clear" in hint else YEL))

    line = paint(" · ", DIM).join(segments) if segments else paint("no session data", DIM)
    # An ASCII mark is dimmed so it recedes; an emoji carries its own colour and
    # is left alone (ANSI dim on an emoji is ignored or muddies it).
    if not TAG:
        return line
    mark = TAG if not TAG.isascii() else paint(TAG, DIM)
    return f"{mark} {line}"


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        # Unparseable input is not worth an error line on every keystroke.
        print(build({}))
        return 0
    try:
        print(build(payload))
    except Exception:
        print(build({}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
