from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .approx import approx_tokens
from .transcripts import response_key, transcript_root

# Discovery ratio: how much of a session was the model FINDING things versus
# DOING things.
#
# A session that greps and lists and reads its way around a repo — one command
# per turn, each re-reading the whole window — spends its turns on discovery,
# not on the change you asked for. That is the pattern behind a one-prompt task
# that runs to dozens of turns: the model is rebuilding a map of the codebase it
# has no memory of, step by step. The fix is not fewer Bash calls, it is giving
# the map UPFRONT (a CLAUDE.md layout, a code-search tool, a pre-loaded index) so
# the model stops rediscovering.
#
# This measures the pattern so it can be seen and, after a change, seen to move:
# discovery turns / (discovery + work) turns, per session. High-and-many is the
# flag — a session that is mostly grep is a session that needed context it did
# not have.
#
# CLASSIFICATION is deterministic, from the tool and (for Bash) the command's
# leading verb. Read-only exploration is discovery; anything that edits, builds,
# tests or commits is work. Delegation (Task) and MCP calls are tracked apart so
# they do not distort the ratio — a subagent's own turns are counted in ITS
# session, not folded into the parent's grep count.

FILE_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead"}
LOOKUP_TOOLS = {"WebFetch", "WebSearch"}

# Bash leading verbs. First matching wins; a command that does real work (an
# edit, a build, a test, a commit) counts as work even if it also greps.
_WORK_BASH = re.compile(
    r"^(npm|pnpm|yarn|pip|pip3|node|python3?|pytest|cargo|make|go|tsc|vitest|jest|"
    r"eslint|prettier|ruff|black|mkdir|rm|mv|cp|touch|tee|chmod|chown|ln|docker|"
    r"kubectl|terraform|psql|sqlite3|ssh|scp|rsync|kill|pkill)\b", re.I)
_WORK_GIT = re.compile(r"^git\s+(commit|add|push|pull|merge|rebase|checkout|switch|"
                       r"reset|stash|tag|cherry-pick|apply|restore|clean|init|mv|rm)\b", re.I)
_SED_INPLACE = re.compile(r"^sed\b.*\s-i\b", re.I)
_REDIRECT_WRITE = re.compile(r"(^|\s)>{1,2}\s*\S")   # a > file redirect (writes)
_DISCOVERY_BASH = re.compile(
    r"^(grep|rg|ag|ack|egrep|fgrep|ls|find|fd|tree|cat|head|tail|less|more|"
    r"wc|file|stat|which|type|pwd|env|printenv|du|df|awk|sed|jq|column|sort|uniq|diff)\b", re.I)
_DISCOVERY_GIT = re.compile(r"^git\s+(log|status|diff|show|blame|grep|ls-files|"
                            r"branch|remote|config|describe|rev-parse|shortlog)\b", re.I)


def classify_bash(cmd: str) -> str:
    """work | discovery | other for one shell command (leading verb).

    Work wins over discovery: a command that mutates is work even if it also
    inspects. `sed -i` and `> file` are writes; a plain `sed`/`awk` filter reads.
    """
    if not cmd:
        return "other"
    c = cmd.strip()
    # take the first stage of a pipe/compound so `grep … | head` reads as grep,
    # but a leading writer (`tee`, redirect) still registers as work.
    head = re.split(r"[;&|]", c, maxsplit=1)[0].strip()
    if _SED_INPLACE.search(c) or _REDIRECT_WRITE.search(head):
        return "work"
    if _WORK_GIT.search(head) or _WORK_BASH.search(head):
        return "work"
    if _DISCOVERY_GIT.search(head) or _DISCOVERY_BASH.search(head):
        return "discovery"
    return "other"


def classify_tool(name: str, cmd: str = "") -> str:
    """work | discovery | delegate | mcp | other for one tool_use."""
    if not name:
        return "other"
    if name in FILE_WRITE_TOOLS:
        return "work"
    if name in READ_TOOLS:
        return "discovery"
    if name in LOOKUP_TOOLS:
        return "discovery"
    if name == "Task":
        return "delegate"
    if name.startswith("mcp__"):
        return "mcp"
    if name == "Bash":
        return classify_bash(cmd)
    return "other"


def _cmd_of(block: dict) -> str:
    inp = block.get("input") or {}
    for f in ("command", "file_path", "pattern", "path", "query", "url"):
        v = inp.get(f)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _parse_ts(ts):
    from datetime import datetime, timezone
    if not ts:
        return None
    try:
        out = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def discovery_report(transcript_root_path=None, *, project=None, window=None,
                     min_tool_calls: int = 20) -> dict:
    """Per-session discovery ratio, over the sessions in scope.

    Counts TOOL CALLS (not turns): a turn can carry several tool_use blocks, and
    the ratio is about the mix of actions. A session below `min_tool_calls` is
    kept but not flagged — a ratio off five calls is noise.
    """
    base = Path(transcript_root_path).expanduser() if transcript_root_path else transcript_root()
    if not base.exists():
        return {"sessions": [], "totals": {"sessions": 0}, "project": project,
                "window": window.label if window is not None else None}

    per: dict[str, dict] = defaultdict(
        lambda: {"project": None, "discovery": 0, "work": 0, "delegate": 0,
                 "mcp": 0, "other": 0, "grep": 0, "first": None, "last": None,
                 "subagent": False,
                 # usage per response (deduped by requestId, last-wins on output
                 # because it streams) so context/output are summed once, not once
                 # per content-block copy. `think` is the effort footprint: the
                 # tokens the thinking budget governs, billed within output.
                 "resp": {}})

    for path in base.rglob("*.jsonl"):
        subagent = "subagents" in str(path)
        cwd_name = None
        seen = set()
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
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                key = response_key(rec) or rec.get("uuid")
                when = _parse_ts(rec.get("timestamp"))
                sid = rec.get("sessionId") or path.stem
                if window is not None and not window.contains(when):
                    # still let non-turn blocks through? no — scope by the turn time
                    if key in seen:
                        pass
                    continue
                e = per[sid]
                e["project"] = e["project"] or cwd_name
                e["subagent"] = e["subagent"] or subagent
                if when is not None:
                    e["first"] = when if e["first"] is None else min(e["first"], when)
                    e["last"] = when if e["last"] is None else max(e["last"], when)
                # Usage for the effort/context split, deduped per response.
                if key:
                    slot = e["resp"].setdefault(key, {"ctx": 0, "out": 0, "think": 0})
                    u = msg.get("usage")
                    if isinstance(u, dict):
                        slot["ctx"] = ((u.get("cache_read_input_tokens") or 0)
                                       + (u.get("cache_creation_input_tokens") or 0)
                                       + (u.get("input_tokens") or 0))
                        slot["out"] = u.get("output_tokens") or 0   # last-wins: output streams
                for b in (msg.get("content") or []):
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "thinking" and key:
                        e["resp"][key]["think"] += approx_tokens(b.get("thinking") or "")
                    if b.get("type") != "tool_use":
                        continue
                    name = b.get("name") or ""
                    cmd = _cmd_of(b)
                    kind = classify_tool(name, cmd)
                    e[kind] = e.get(kind, 0) + 1
                    if kind == "discovery" and (name == "Bash"):
                        if re.match(r"^\s*(grep|rg|ag|ack|egrep|fgrep)\b", cmd, re.I):
                            e["grep"] += 1

    if project:
        per = {k: v for k, v in per.items() if v["project"] == project}

    rows = []
    corpus_ctx = corpus_out = corpus_think = 0
    for sid, e in per.items():
        acted = e["discovery"] + e["work"]
        total = acted + e["delegate"] + e["mcp"] + e["other"]
        if total == 0:
            continue
        ratio = round(e["discovery"] / acted * 100, 1) if acted else None
        ctx = sum(r["ctx"] for r in e["resp"].values())
        out = sum(r["out"] for r in e["resp"].values())
        think = sum(r["think"] for r in e["resp"].values())
        corpus_ctx += ctx; corpus_out += out; corpus_think += think
        # Generation share = output (which is where the thinking budget bills) as
        # a fraction of everything the model touched. Low = the session is context
        # re-read, so EFFORT is not the lever, however you set it.
        denom = ctx + out
        gen_share = round(out / denom * 100, 2) if denom else None
        think_share = round(think / denom * 100, 2) if (denom and think) else None
        rows.append({
            "session": sid[:8], "transcript": sid, "project": e["project"],
            "subagent": e["subagent"],
            "discovery": e["discovery"], "work": e["work"], "grep": e["grep"],
            "delegate": e["delegate"], "mcp": e["mcp"], "other": e["other"],
            "tool_calls": total, "acted": acted, "discovery_ratio": ratio,
            "output_tokens": out, "thinking_tokens": think, "context_tokens": ctx,
            "gen_share": gen_share, "thinking_share": think_share,
            # The flag: enough calls to mean something, and mostly finding rather
            # than doing. That is the session that needed context it did not have.
            "high_discovery": acted >= min_tool_calls and ratio is not None and ratio >= 60.0,
        })
    rows.sort(key=lambda r: (-(r["discovery_ratio"] or 0) if r["acted"] >= min_tool_calls else 0,
                             -r["discovery"]))
    flagged = [r for r in rows if r["high_discovery"]]
    tot_disc = sum(r["discovery"] for r in rows)
    tot_work = sum(r["work"] for r in rows)
    return {
        "sessions": rows,
        "totals": {
            "sessions": len(rows),
            "high_discovery_sessions": len(flagged),
            "discovery_calls": tot_disc,
            "work_calls": tot_work,
            "grep_calls": sum(r["grep"] for r in rows),
            "overall_ratio": round(tot_disc / (tot_disc + tot_work) * 100, 1) if (tot_disc + tot_work) else None,
            # Corpus generation share — the headline answer to "is effort our cost".
            "gen_share": round(corpus_out / (corpus_ctx + corpus_out) * 100, 2) if (corpus_ctx + corpus_out) else None,
            "thinking_share": round(corpus_think / (corpus_ctx + corpus_out) * 100, 2) if (corpus_ctx + corpus_out and corpus_think) else None,
        },
        "min_tool_calls": min_tool_calls,
        "project": project,
        "window": window.label if window is not None else None,
    }
