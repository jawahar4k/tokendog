from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .transcripts import transcript_root

# Which tools fail, how often, and how — the deterministic half of tuneloop's
# "this tool keeps failing" card.
#
# A failing tool is expensive twice: the failed call spends tokens, and the
# retry that follows spends them again on a bigger context. tuneloop reads the
# error text with an LLM to suggest a fix; tokendog stays deterministic —
# it categorises the failure by pattern and reports the rate, which is enough to
# decide WHICH tool to look at. No model call, so it is free and never adds to
# the very bill it is measuring.
#
# THE JOIN. A tool_result records success/failure but NOT the tool's name; the
# name is on the tool_use it answers, in an earlier assistant turn. So the
# reader holds tool_use ids -> names as it goes and resolves each tool_result
# against them. A result whose id is never seen (a truncated transcript) is
# counted as "unknown" rather than dropped, so totals stay honest.

# Ordered: the first pattern that matches wins, so put the specific ones first.
CATEGORIES = (
    ("timeout", re.compile(r"tim(?:e|ed)\s*out|timeout|deadline exceeded", re.I)),
    ("not-found", re.compile(r"no such file|not found|does not exist|cannot find|ENOENT|"
                             r"no matches found", re.I)),
    ("permission", re.compile(r"permission denied|EACCES|not permitted|forbidden|unauthor", re.I)),
    ("network", re.compile(r"ECONNREFUSED|connection refused|network|ETIMEDOUT|getaddrinfo|"
                           r"could not resolve|unreachable", re.I)),
    ("rate-limit", re.compile(r"rate limit|429|too many requests|quota", re.I)),
    ("syntax", re.compile(r"syntaxerror|parse error|unexpected token|invalid syntax", re.I)),
    ("interrupted", re.compile(r"interrupted|cancell?ed|aborted|user rejected|SIGINT", re.I)),
    ("nonzero-exit", re.compile(r"exit code [1-9]|exited with|command failed|non-zero", re.I)),
)


def categorise(text: str) -> str:
    """A short, deterministic label for one failure. `other` when nothing fits."""
    if not text:
        return "other"
    for name, pat in CATEGORIES:
        if pat.search(text):
            return name
    return "other"


def _result_text(content) -> str:
    """Flatten a tool_result's content to a string for pattern-matching."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or "")
            else:
                parts.append(str(b))
        return " ".join(str(p) for p in parts)
    return str(content)


def _parse_ts(ts):
    from datetime import datetime, timezone
    if not ts:
        return None
    try:
        out = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def tool_errors(transcript_root_path=None, *, project=None, window=None) -> dict:
    """Per-tool call and error counts, with a category breakdown for the failures.

    `window` filters by the RESULT's turn time, so "errors in the last 20 min"
    is answerable. Reads transcripts directly: the hook sink records tool
    payloads but not success/failure, and it only covers sessions started after
    the plugin was installed.
    """
    base = Path(transcript_root_path).expanduser() if transcript_root_path else transcript_root()
    if not base.exists():
        return {"tools": [], "totals": {"calls": 0, "errors": 0}, "project": project,
                "window": window.label if window is not None else None}

    per: dict[str, dict] = defaultdict(
        lambda: {"tool": None, "calls": 0, "errors": 0, "categories": defaultdict(int),
                 "sample": None, "projects": set()})

    for path in base.rglob("*.jsonl"):
        names: dict[str, str] = {}      # tool_use id -> tool name, within this file
        cwd_name = None
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                cwd = rec.get("cwd")
                if isinstance(cwd, str) and cwd and cwd_name is None:
                    cwd_name = Path(cwd.rstrip("/")).name
                if project and cwd_name and cwd_name != project:
                    # keep reading (a file could in principle change cwd) but skip attribution
                    pass
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                if rec.get("type") == "assistant":
                    for b in (msg.get("content") or []):
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            names[b.get("id")] = b.get("name") or "?"
                elif rec.get("type") == "user":
                    when = _parse_ts(rec.get("timestamp"))
                    if window is not None and not window.contains(when):
                        continue
                    for b in (msg.get("content") or []):
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        name = names.get(b.get("tool_use_id"), "unknown")
                        if project and cwd_name and cwd_name != project:
                            continue
                        e = per[name]
                        e["tool"] = name
                        e["calls"] += 1
                        if cwd_name:
                            e["projects"].add(cwd_name)
                        if b.get("is_error"):
                            e["errors"] += 1
                            cat = categorise(_result_text(b.get("content")))
                            e["categories"][cat] += 1
                            if e["sample"] is None:
                                txt = " ".join(_result_text(b.get("content")).split())
                                e["sample"] = txt[:160]

    rows = []
    for name, e in per.items():
        if not e["calls"]:
            continue
        rate = round(e["errors"] / e["calls"] * 100, 1)
        dom = max(e["categories"].items(), key=lambda kv: kv[1])[0] if e["categories"] else None
        rows.append({
            "tool": name,
            "calls": e["calls"],
            "errors": e["errors"],
            "error_rate": rate,
            "dominant": dom,
            "categories": dict(e["categories"]),
            "sample": e["sample"],
            "projects": sorted(e["projects"]),
            # High-error flag: enough calls to be real, and failing often. A tool
            # called twice and failing once is noise, not a pattern.
            "high_error": e["calls"] >= 5 and rate >= 20.0,
        })
    rows.sort(key=lambda r: (-r["errors"], -r["error_rate"]))
    return {
        "tools": rows,
        "totals": {
            "calls": sum(r["calls"] for r in rows),
            "errors": sum(r["errors"] for r in rows),
            "high_error_tools": sum(1 for r in rows if r["high_error"]),
        },
        "project": project,
        "window": window.label if window is not None else None,
    }
