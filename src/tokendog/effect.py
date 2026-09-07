"""Did an intervention work?

TokenDog prices the bill precisely and, until this module, could not say
whether anything you changed helped. `savings` only ever measured output
truncation, so with truncation off it reported 0.0% forever — which reads as
"nothing worked" when it actually means "nothing was measured".

Everything here comes from transcripts already on disk. No new hooks, no
instrumentation to install, and it works retroactively: you can measure a
change you made last week.

Three questions, because they are the three that turn a number into a decision:

  1. Are tool payloads getting smaller?  Tool results become carried context,
     so their size is the input to the 57% bucket. Joins `tool_use` to its
     `tool_result` to get the real returned size, per tool.
  2. Is scoping actually happening?  `Read` with `offset`/`limit` is the
     frugal skill's most specific instruction and the only one that is
     checkable from the record, so it is the honest proxy for compliance.
  3. What does a turn cost as a session ages?  Context is re-read every turn,
     so a late turn costs a multiple of an early one. That ratio is what makes
     "split this stage" a rankable action rather than a hunch.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .backend import event_cost
from .transcripts import project_of, read_transcript, transcript_root

# Turn-index buckets. Open-ended at the top: long agent loops are the case
# this exists to price, so they must not be folded into the last closed band.
POSITION_BUCKETS = ((1, 10), (11, 25), (26, 50), (51, 100), (101, None))


@dataclass
class ToolCall:
    ts: str
    tool: str
    project: str | None
    result_bytes: int
    scoped: bool | None = None      # None when scoping does not apply to this tool


def _blocks(record):
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return content if isinstance(content, list) else []


def _is_scoped(tool: str, tool_input) -> bool | None:
    """True/False for tools where narrowing is expressible, else None.

    Only counted where the record can actually answer it. Charging a tool with
    no scoping parameters as 'unscoped' would invent non-compliance.
    """
    if not isinstance(tool_input, dict):
        return None
    if tool == "Read":
        return bool(tool_input.get("offset") or tool_input.get("limit"))
    if tool == "Grep":
        return bool(tool_input.get("head_limit")
                    or tool_input.get("path") or tool_input.get("glob")
                    or tool_input.get("output_mode") in ("files_with_matches", "count"))
    return None


def iter_tool_calls(root=None, project=None):
    """Yield one ToolCall per tool_use that has a matching tool_result.

    `tool_use` is emitted by the assistant and its `tool_result` arrives in the
    following user record, so the pair is joined by id within a transcript.
    """
    base = Path(root).expanduser() if root is not None else transcript_root()
    if not base.exists():
        return
    for jf in sorted(base.rglob("*.jsonl")):
        pending: dict[str, tuple] = {}
        try:
            handle = jf.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                proj = project_of(record)
                for blk in _blocks(record):
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("type") == "tool_use":
                        pending[blk.get("id")] = (
                            blk.get("name") or "unknown",
                            record.get("timestamp") or "",
                            proj,
                            _is_scoped(blk.get("name") or "", blk.get("input")),
                        )
                    elif blk.get("type") == "tool_result":
                        found = pending.pop(blk.get("tool_use_id"), None)
                        if not found:
                            continue
                        name, ts, call_proj, scoped = found
                        if project and call_proj != project:
                            continue
                        body = blk.get("content")
                        size = len(body) if isinstance(body, str) else len(json.dumps(body))
                        yield ToolCall(ts=ts, tool=name, project=call_proj,
                                       result_bytes=size, scoped=scoped)


def tool_summary(calls) -> dict:
    """Per-tool volume and scoping, plus a total row."""
    per = defaultdict(lambda: {"calls": 0, "bytes": 0, "scoped": 0, "scopeable": 0})
    for c in calls:
        e = per[c.tool]
        e["calls"] += 1
        e["bytes"] += c.result_bytes
        if c.scoped is not None:
            e["scopeable"] += 1
            e["scoped"] += 1 if c.scoped else 0
    for e in per.values():
        e["mean_bytes"] = e["bytes"] // e["calls"] if e["calls"] else 0
        e["scoped_pct"] = (e["scoped"] / e["scopeable"] * 100) if e["scopeable"] else None
    total_calls = sum(e["calls"] for e in per.values())
    total_bytes = sum(e["bytes"] for e in per.values())
    return {"tools": dict(per), "calls": total_calls, "bytes": total_bytes,
            "mean_bytes": total_bytes // total_calls if total_calls else 0}


def position_curve(root=None, project=None) -> dict:
    """What a turn costs as a function of how deep into the session it is.

    Cost is context x turns, so this is where the compounding shows up: the
    same work late in a long session costs a multiple of what it cost early.
    The ratio is the argument for splitting a stage, expressed in money.
    """
    base = Path(root).expanduser() if root is not None else transcript_root()
    rows = {b: {"turns": 0, "cost": 0.0, "context": 0} for b in POSITION_BUCKETS}
    if not base.exists():
        return {"buckets": [], "ratio": None}
    for jf in sorted(base.rglob("*.jsonl")):
        index = 0
        for event in read_transcript(jf):
            if project and event.project != project:
                continue
            index += 1
            for lo, hi in POSITION_BUCKETS:
                if index >= lo and (hi is None or index <= hi):
                    r = rows[(lo, hi)]
                    r["turns"] += 1
                    r["cost"] += event_cost(event)
                    r["context"] += event.context_tokens
                    break
    out = []
    for (lo, hi) in POSITION_BUCKETS:
        r = rows[(lo, hi)]
        if not r["turns"]:
            continue
        out.append({"label": f"{lo}-{hi}" if hi else f"{lo}+",
                    "turns": r["turns"],
                    "cost_per_turn": r["cost"] / r["turns"],
                    "context_per_turn": r["context"] // r["turns"]})
    ratio = (out[-1]["cost_per_turn"] / out[0]["cost_per_turn"]
             if len(out) > 1 and out[0]["cost_per_turn"] else None)
    return {"buckets": out, "ratio": ratio}


# A 5-minute cache costs 1.25x input to write, a 1-hour cache 2x. The 1h TTL is
# only worth its premium if turns are far enough apart that a 5m cache would
# have died and forced a full prefix rebuild. That is an empirical question and
# the answer is not obvious: on a fast agent loop the gaps are seconds, which
# argues for 5m — until you price the handful of long pauses, which do not.
FIVE_MIN, ONE_HOUR = 300.0, 3600.0


def cache_efficiency(root=None, project=None, model="opus") -> dict:
    """Is the cache earning its keep, and is the TTL the right one?

    Answers with the counterfactual rather than the ratio: what the same work
    would have cost on the other TTL, including the rebuilds that TTL would
    have forced. A write:read ratio alone looks alarming early in a session and
    tells you nothing about what to do.
    """
    from datetime import datetime
    from .pricing import (CACHE_WRITE_5M_MULTIPLIER, PRICES, estimate_cost)

    base = Path(root).expanduser() if root is not None else transcript_root()
    if not base.exists():
        return {}
    five = one = 0
    reads = 0
    rebuild_tokens = 0
    expiring_gaps = 0
    for jf in sorted(base.rglob("*.jsonl")):
        prev = None
        for e in read_transcript(jf):
            if project and e.project != project:
                continue
            five += e.cache_creation_5m_tokens
            one += e.cache_creation_1h_tokens
            reads += e.cache_read_tokens
            try:
                ts = datetime.fromisoformat(e.ts)
            except (TypeError, ValueError):
                continue
            if prev is not None:
                gap = (ts - prev).total_seconds()
                if FIVE_MIN < gap <= ONE_HOUR:
                    # A 5m cache is dead here and a 1h cache is not, so this is
                    # exactly the prefix a downgrade would have to rebuild.
                    expiring_gaps += 1
                    rebuild_tokens += e.cache_read_tokens
            prev = ts

    if not (five + one):
        return {}
    actual = estimate_cost(model=model, cache_creation_5m_tokens=five,
                           cache_creation_1h_tokens=one)
    all_five = estimate_cost(model=model, cache_creation_5m_tokens=five + one)
    rebuilds = (rebuild_tokens / 1_000_000) * PRICES[model][0] * CACHE_WRITE_5M_MULTIPLIER
    return {"write_5m": five, "write_1h": one, "reads": reads,
            "actual_usd": actual, "all_5m_usd": all_five,
            "rebuild_usd": rebuilds, "expiring_gaps": expiring_gaps,
            "net_usd": (all_five + rebuilds) - actual,
            "write_read_pct": ((five + one) / reads * 100) if reads else None}


def compare(before: dict, after: dict) -> dict:
    """Percentage change between two tool_summary results."""
    def delta(a, b):
        return ((b - a) / a * 100) if a else None
    tools = {}
    for name in sorted(set(before["tools"]) | set(after["tools"])):
        b = before["tools"].get(name)
        a = after["tools"].get(name)
        if not b or not a:
            continue
        tools[name] = {
            "before_calls": b["calls"], "after_calls": a["calls"],
            "before_mean": b["mean_bytes"], "after_mean": a["mean_bytes"],
            "change_pct": delta(b["mean_bytes"], a["mean_bytes"]),
            "before_scoped_pct": b["scoped_pct"], "after_scoped_pct": a["scoped_pct"],
        }
    return {"tools": tools,
            "before_mean": before["mean_bytes"], "after_mean": after["mean_bytes"],
            "change_pct": delta(before["mean_bytes"], after["mean_bytes"])}


def split_calls(calls, split: str):
    """Partition tool calls into (before, on-or-after) a YYYY-MM-DD boundary."""
    before, after = [], []
    for c in calls:
        (after if (c.ts or "")[:10] >= split else before).append(c)
    return before, after
