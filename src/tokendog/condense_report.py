from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .approx import approx_tokens
from .condense import condense
from .limit_resume import is_reset
from .transcripts import response_key, transcript_root

# The condenser's savings, for the dashboard — two figures side by side:
#
#   PROJECTED   What condensing the large tool results in the selected range
#               WOULD save, by actually running the deterministic condenser over
#               each one and applying the re-read multiplier (a saved token is
#               re-read on every later turn in its segment, so the saving is
#               size-reduction × turns-after). No worker call — this is the free,
#               deterministic floor, so it is a conservative estimate of a live
#               run (the Haiku tier would only add to it).
#
#   RECORDED    What the hook has ACTUALLY logged in shadow/enforce mode
#               (`savings.savings_summary`). Zero until you turn the hook on;
#               this is the number to watch accrue before enforcing.
#
# Same re-read model as `session_detail`/`floor`: additions are charged size ×
# turns they are re-read before the next reset. The projection is intentionally
# an UPPER-BOUND-ish figure — it assumes every large result was condensable — and
# says so, so the dashboard never overstates a guaranteed saving.

FILE_RESULT_TOOLS = ("Read", "Bash", "Grep", "WebFetch")


def _payload_tokens(content) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return approx_tokens(content)
    try:
        return approx_tokens(json.dumps(content, default=str))
    except (TypeError, ValueError):
        return approx_tokens(str(content))


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    # A tool_result's content is often a list of blocks ({"type":"text",...});
    # join their text so the condenser sees the real output, not JSON scaffolding.
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        if parts:
            return "\n".join(parts)
    try:
        return json.dumps(content, default=str)
    except (TypeError, ValueError):
        return str(content)


def _parse(ts):
    from datetime import datetime, timezone
    if not ts:
        return None
    try:
        out = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def projected_savings(transcript_root_path=None, *, project=None, window=None,
                      threshold: int = 2000) -> dict:
    """Run the deterministic condenser over large results in range; sum the
    re-read-weighted saving. No worker/network."""
    base = Path(transcript_root_path).expanduser() if transcript_root_path else transcript_root()
    if not base.exists():
        return {"saved": 0, "candidates": 0, "results": 0, "by_tool": {}}
    saved = 0
    candidates = total_results = 0
    by_tool: dict[str, dict] = defaultdict(lambda: {"n": 0, "saved": 0})

    for path in base.rglob("*.jsonl"):
        resp: dict = {}
        order: list = []
        uses: dict = {}       # tool_use_id -> (name, command)
        results: dict = {}    # tool_use_id -> (tokens, text)
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
                m = rec.get("message")
                if not isinstance(m, dict):
                    continue
                if rec.get("type") == "assistant":
                    k = response_key(rec) or rec.get("uuid")
                    when = _parse(rec.get("timestamp"))
                    if k not in resp:
                        resp[k] = {"ctx": 0, "at": when, "tools": []}
                        order.append(k)
                    u = m.get("usage")
                    if isinstance(u, dict):
                        resp[k]["ctx"] = ((u.get("cache_read_input_tokens") or 0)
                                          + (u.get("cache_creation_input_tokens") or 0)
                                          + (u.get("input_tokens") or 0))
                    for b in (m.get("content") or []):
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            cmd = (b.get("input") or {}).get("command", "") if isinstance(b.get("input"), dict) else ""
                            uses[b.get("id")] = (b.get("name") or "?", cmd)
                            resp[k]["tools"].append(b.get("id"))
                elif rec.get("type") == "user":
                    for b in (m.get("content") or []):
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            results[b.get("tool_use_id")] = b.get("content")

        if project and cwd_name and cwd_name != project:
            continue
        turns = [resp[k] for k in order if resp[k]["ctx"] > 0]
        if len(turns) < 2:
            continue
        # window scope by turn time
        if window is not None:
            turns = [t for t in turns if window.contains(t["at"])]
            if len(turns) < 2:
                continue
        # segment boundaries for the re-read multiplier
        bounds = []
        start = 0
        for i in range(1, len(turns)):
            if is_reset(turns[i - 1]["ctx"], turns[i]["ctx"]):
                bounds.append((start, i)); start = i
        bounds.append((start, len(turns)))
        hi_of = [0] * len(turns)
        for lo, hi in bounds:
            for i in range(lo, hi):
                hi_of[i] = hi

        for i, t in enumerate(turns):
            reread = hi_of[i] - i - 1
            for tid in t["tools"]:
                content = results.get(tid)
                if content is None:
                    continue
                total_results += 1
                name, cmd = uses.get(tid, ("?", ""))
                if name not in FILE_RESULT_TOOLS:
                    continue
                rtok = _payload_tokens(content)
                if rtok < threshold:
                    continue
                rep = condense(_result_text(content), tool_name=name, command=cmd,
                               use_worker=False)
                if not rep["reduced"]:
                    continue
                candidates += 1
                cut = (rep["raw_tokens"] - rep["digest_tokens"]) * max(0, reread)
                saved += cut
                e = by_tool[name]
                e["n"] += 1
                e["saved"] += cut

    return {"saved": saved, "candidates": candidates, "results": total_results,
            "by_tool": {k: v for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1]["saved"])}}


def savings_report(transcript_root_path=None, *, project=None, window=None) -> dict:
    """Projected (deterministic, this range) + recorded (ledger) side by side."""
    from .savings import savings_summary
    proj = projected_savings(transcript_root_path, project=project, window=window)
    try:
        recorded = savings_summary()
    except Exception:
        recorded = {"events": 0, "total_saved": 0, "pct": 0.0, "per_tool": {}, "modes": {}}
    return {
        "projected": proj,
        "recorded": recorded,
        "project": project,
        "window": window.label if window is not None else None,
    }
