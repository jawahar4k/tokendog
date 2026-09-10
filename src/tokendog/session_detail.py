from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .approx import approx_tokens
from .hygiene import segment
from .limit_resume import is_reset
from .surface import load_inventory, local_surface
from .transcripts import response_key, transcript_root

# Turn-by-turn: what a single session's window was made of, and what each
# addition went on to cost.
#
# The reports above this one rank sessions. This one opens ONE of them, because
# "285 turns and 74.9M input" is a verdict without an explanation — it says a
# session was expensive and nothing about which reads made it so.
#
# THE MODEL. Occupancy only grows within a context, so the difference between
# consecutive turns is what ENTERED at that turn. What entered is attributable:
# the tool results returned to the model, plus the previous turn's own output.
# Anything left over is a residual — a typed message, an injected reminder — and
# is reported as a residual rather than assigned to whichever category is
# nearest.
#
# THE COST. A turn's addition is re-read by every later turn in the same
# context, so its cost is its size times the turns that followed it BEFORE THE
# NEXT RESET. Counting past a reset would charge a read for turns that never saw
# it; segments therefore never overlap and the total stays bounded by what the
# session actually carried.
#
# THE BASELINE. The first turn of a context already carries the system prompt,
# every enabled tool's schema, and the instruction files — before a single word
# is typed. It is decomposed as far as the data honestly allows: MCP schema
# sizes come from the surface inventory when one has been captured, skills and
# instruction files are read from disk, and the REMAINDER is left labelled as
# remainder. The API does not report what is inside a cached prefix, so any
# finer split would be invention.

BYTES_PER_TOKEN = 3.6  # only used when a payload cannot be re-encoded


@dataclass
class Turn:
    index: int
    at: str
    context: int
    delta: int
    carried: int
    turns_after: int
    tools: list
    tool_tokens: int
    output_tokens: int
    thinking_tokens: int
    residual: int
    segment: int
    reset_before: bool
    in_window: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


def find_transcript(session: str, root=None) -> Path | None:
    """Locate a transcript by full id or by any unique prefix."""
    base = Path(root).expanduser() if root else transcript_root()
    if not base.exists():
        return None
    exact = [p for p in base.rglob("*.jsonl") if p.stem == session]
    if exact:
        return exact[0]
    matches = [p for p in base.rglob("*.jsonl") if p.stem.startswith(session)]
    return matches[0] if len(matches) == 1 else None


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        out = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def _payload_tokens(content) -> int:
    """Tokens for a tool result, counted rather than estimated where possible."""
    if content is None:
        return 0
    if isinstance(content, str):
        return approx_tokens(content)
    try:
        return approx_tokens(json.dumps(content, default=str))
    except (TypeError, ValueError):
        return int(len(str(content)) / BYTES_PER_TOKEN)


def read_turns(path) -> list[dict]:
    """One record per API response: occupancy, the tools it called, its output.

    Responses are grouped on `requestId` for the same reason the ingest does:
    a response is written once per content block and each copy repeats its
    usage, so counting records would double every figure here.
    """
    responses: dict[str, dict] = {}
    order: list[str] = []
    results: dict[str, int] = {}
    entrypoint = None
    project = None

    with Path(path).open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            entrypoint = rec.get("entrypoint") or entrypoint
            cwd = rec.get("cwd")
            if isinstance(cwd, str) and cwd and project is None:
                project = Path(cwd.rstrip("/")).name
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            if rec.get("type") == "assistant":
                key = response_key(rec) or rec.get("uuid")
                if key not in responses:
                    responses[key] = {"at": rec.get("timestamp"), "usage": None,
                                      "tools": [], "output": 0, "thinking": 0}
                    order.append(key)
                slot = responses[key]
                usage = message.get("usage")
                if isinstance(usage, dict):
                    slot["usage"] = usage           # last wins: output streams
                    slot["at"] = rec.get("timestamp") or slot["at"]
                for block in (message.get("content") or []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        slot["tools"].append({"id": block.get("id"),
                                              "name": block.get("name") or "?",
                                              "input": block.get("input") or {}})
                    elif block.get("type") == "thinking":
                        slot["thinking"] += approx_tokens(block.get("thinking") or "")
            elif rec.get("type") == "user":
                for block in (message.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        results[block.get("tool_use_id")] = _payload_tokens(block.get("content"))

    out = []
    for key in order:
        slot = responses[key]
        usage = slot["usage"] or {}
        ctx = ((usage.get("cache_read_input_tokens") or 0)
               + (usage.get("cache_creation_input_tokens") or 0)
               + (usage.get("input_tokens") or 0))
        if ctx <= 0:
            continue
        tools = [{"name": t["name"],
                  "tokens": results.get(t["id"], 0),
                  "detail": _describe(t)} for t in slot["tools"]]
        out.append({
            "at": _parse(slot["at"]),
            "context": ctx,
            "output": usage.get("output_tokens") or 0,
            "thinking": slot["thinking"],
            "tools": tools,
            "tool_tokens": sum(t["tokens"] for t in tools),
        })
    return [r for r in out if r["at"] is not None], entrypoint, project


def _describe(tool: dict) -> str:
    """A short, recognisable label for what the call actually did."""
    data = tool.get("input") or {}
    for field in ("command", "file_path", "pattern", "path", "url", "prompt", "query"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:120]
    return ""


def baseline_breakdown(first_context: int, *, home=None, inventory=None) -> dict:
    """Split the floor every turn pays into the parts that can be named.

    Anything not attributable stays in `remainder` — the API does not report
    what a cached prefix contains, so a finer split would be made up.
    """
    inv = (inventory if inventory is not None else load_inventory()).get("tools", {})
    schema = sum(int(v or 0) for v in inv.values()) or None
    local = local_surface(home)
    skills = sum(s["always_on_tokens"] for s in local["skills"])
    instructions = sum(i["tokens"] for i in local["instructions"])
    known = (schema or 0) + skills + instructions
    return {
        "total": first_context,
        "tool_schemas": schema,
        "skills_always_on": skills,
        "instruction_files": instructions,
        "remainder": max(0, first_context - known),
        "remainder_note": ("system prompt, conversation seed, and any connector not "
                           "in the inventory"),
        "have_inventory": bool(inv),
    }


def _totals(turns: list) -> dict:
    """Sum a list of turns. Used for the whole session and for a window slice.

    Written once because the two blocks sat side by side and would otherwise
    drift — a field added to one and not the other is invisible until a reader
    compares them and finds the slice missing a column the total has.
    """
    if not turns:
        return {"turns": 0, "input": 0, "output": 0, "tool_tokens": 0,
                "thinking": 0, "residual": 0, "carried": 0,
                "peak_context": 0, "burn_per_min": None,
                "first": None, "last": None}
    span_min = (turns[-1].at_dt - turns[0].at_dt).total_seconds() / 60.0
    total_input = sum(t.context for t in turns)
    return {
        "turns": len(turns),
        "input": total_input,
        "output": sum(t.output_tokens for t in turns),
        "tool_tokens": sum(t.tool_tokens for t in turns),
        "thinking": sum(t.thinking_tokens for t in turns),
        "residual": sum(t.residual for t in turns),
        "carried": sum(t.carried for t in turns),
        "peak_context": max(t.context for t in turns),
        "burn_per_min": round(total_input / span_min, 1) if span_min > 0 else None,
        "first": turns[0].at,
        "last": turns[-1].at,
    }


def session_detail(session: str, *, root=None, home=None, top: int = 15,
                   window=None) -> dict:
    """Turn-by-turn drilldown for one session.

    A window MARKS turns rather than removing them. Every figure here is
    relative to the whole session — a turn's carry cost is its size times the
    turns that re-read it, and both the floor it sits on and the turns that
    followed it exist outside any window a reader happens to ask about.
    Dropping the out-of-window turns would leave the first surviving turn
    looking like the start of a context, so its whole carried window would be
    reported as though it had just arrived, and the baseline breakdown would be
    computed against a number that is not a baseline at all.

    So: `turns` keeps every turn, each flagged `in_window`; `window_totals`
    reports what the slice cost; and `top_turns` ranks within the slice, since
    that is the list a reader asked the window for.
    """
    path = find_transcript(session, root)
    if path is None:
        return {"session": session, "found": False,
                "error": "no transcript matches that id (try more characters)"}

    rows, entrypoint, project = read_turns(path)
    if not rows:
        return {"session": session, "found": False,
                "error": "transcript has no metered turns"}

    rows.sort(key=lambda r: r["at"])
    contexts = [(r["at"], r["context"]) for r in rows]
    segs = segment(contexts)

    # Segment boundaries, so a turn's cost stops at the reset that discarded it.
    bounds = []
    start = 0
    for i in range(1, len(rows)):
        if is_reset(rows[i - 1]["context"], rows[i]["context"]):
            bounds.append((start, i))
            start = i
    bounds.append((start, len(rows)))

    turns: list[Turn] = []
    for seg_no, (lo, hi) in enumerate(bounds, start=1):
        for i in range(lo, hi):
            r = rows[i]
            prev = rows[i - 1]["context"] if i > lo else 0
            delta = r["context"] - prev if i > lo else r["context"]
            after = hi - i - 1
            accounted = r["tool_tokens"] + (rows[i - 1]["output"] if i > lo else 0)
            turn = Turn(
                index=i + 1, at=r["at"].astimezone().isoformat(timespec="minutes"),
                context=r["context"], delta=delta,
                carried=max(0, delta) * after, turns_after=after,
                tools=r["tools"], tool_tokens=r["tool_tokens"],
                output_tokens=r["output"], thinking_tokens=r["thinking"],
                residual=max(0, delta - accounted) if i > lo else 0,
                segment=seg_no, reset_before=(i == lo and lo > 0),
                in_window=(window is None or window.contains(r["at"])))
            # The instant, kept off the dataclass so it stays out of `as_dict`
            # and the JSON export — `at` is the serialised form, and two
            # spellings of the same timestamp in one payload invite the reader
            # to pick the wrong one.
            turn.at_dt = r["at"]
            turns.append(turn)

    inside = [t for t in turns if t.in_window]
    ranked = sorted(inside, key=lambda t: -t.carried)[:top]
    by_tool: dict[str, dict] = {}
    for t in inside:
        for tool in t.tools:
            e = by_tool.setdefault(tool["name"], {"tool": tool["name"], "calls": 0,
                                                  "tokens": 0, "carried": 0})
            e["calls"] += 1
            e["tokens"] += tool["tokens"]
            share = (t.carried / len(t.tools)) if t.tools else 0
            e["carried"] += int(share)

    return {
        "session": path.stem, "found": True, "path": path.name,
        "project": project, "entrypoint": entrypoint,
        "headless": bool(entrypoint) and entrypoint != "cli",
        "turns": [t.as_dict() for t in turns],
        "segments": [dict(number=i + 1, **s) for i, s in enumerate(segs)],
        "baseline": baseline_breakdown(rows[0]["context"], home=home),
        "top_turns": [t.as_dict() for t in ranked],
        "by_tool": sorted(by_tool.values(), key=lambda r: -r["carried"]),
        "window": window.label if window is not None else None,
        "window_totals": _totals(inside) if window is not None else None,
        "totals": _totals(turns),
    }
