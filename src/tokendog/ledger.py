from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Split the window by WHAT THE READER CAN DO ABOUT IT.
#
# Everything in the context window is re-sent on every turn, so the only useful
# question about a token is which lever moves it:
#
#   setup  re-sent definitions. Shrunk by DISABLING something — a connector, a
#          skill, an agent. `/clear` does not touch it; it is re-injected.
#   work   the history. Shrunk by `/clear` or `/compact`, and by nothing else.
#
# A percentage cannot tell those apart, which is why "83% full" leads people to
# compact a session whose weight is a connector they never call.
#
# THE ONE RULE: totals are exact, only the split is estimated. Each turn's total
# comes from the API's own `usage`; the growth since the previous turn is shared
# across whatever arrived in between, in proportion. Nothing here ever invents a
# total, and nothing here is ever priced — `pricing` does that, from usage.

SETUP_KINDS = ("sys", "mcp", "skills", "agents")
WORK_KINDS = ("tools", "mcp_results", "skill_results", "chat", "other")

# Tokens per naive chars/4 unit. Real transcript text runs nearer 2.1 chars per
# token than 4, so chars/4 understates by roughly this much. A session measures
# its own value once it has seen enough; this is the starting point.
DEFAULT_RATIO = 1.9
RATIO_MIN, RATIO_MAX = 1.0, 4.0
RATIO_MIN_TURN_CHARS = 1_000      # a turn too small to be evidence of anything
RATIO_MIN_OBSERVED = 20_000       # measure only once this much has been seen

# Attachment kinds that carry setup, and the bucket each lands in. Keys are the
# attachment `type`; values are (bucket, field, shape).
_SETUP_ATTACHMENTS = {
    "prompt_snapshot":        ("sys", "systemPrompt", "list"),
    "instructions":           ("sys", "files", "files"),
    "skill_listing":          ("skills", "content", "str"),
    "agent_listing_delta":    ("agents", "addedLines", "list"),
    "deferred_tools_delta":   ("mcp", "addedLines", "list"),
    "deferred_tools_record":  ("mcp", "entries", "entries"),
    "mcp_instructions_delta": ("mcp", "addedBlocks", "list"),
}
# Attachment kinds that are work: content pulled in for this task.
_WORK_ATTACHMENTS = {"file": "tools", "edited_text_file": "tools", "directory": "tools"}


@dataclass
class Split:
    """One session's window, divided by lever. Values are tokens."""

    setup: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SETUP_KINDS, 0))
    work: dict[str, int] = field(default_factory=lambda: dict.fromkeys(WORK_KINDS, 0))
    total: int = 0
    ratio: float = DEFAULT_RATIO
    turns: int = 0
    segments: int = 0
    observed_chars: int = 0

    @property
    def setup_total(self) -> int:
        return sum(self.setup.values())

    @property
    def work_total(self) -> int:
        return sum(self.work.values())

    def as_dict(self) -> dict:
        return {"setup": dict(self.setup), "work": dict(self.work),
                "setup_total": self.setup_total, "work_total": self.work_total,
                "total": self.total, "ratio": round(self.ratio, 3),
                "turns": self.turns, "segments": self.segments}


def _chars(value) -> int:
    """Characters in a string, a list of strings, or a list of records."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        n = 0
        for item in value:
            if isinstance(item, str):
                n += len(item)
            elif isinstance(item, dict):
                n += sum(len(v) for v in item.values() if isinstance(v, str))
        return n
    return 0


def _result_bucket(tool_name: str) -> str:
    if tool_name.startswith("mcp__"):
        return "mcp_results"
    if tool_name in ("Skill", "SlashCommand"):
        return "skill_results"
    return "tools"


def _usage_total(usage: dict) -> int:
    """What the window held for this turn, from the API's own numbers."""
    return int((usage.get("input_tokens") or 0)
               + (usage.get("cache_creation_input_tokens") or 0)
               + (usage.get("cache_read_input_tokens") or 0))


class _Accumulator:
    """Pending characters per category, settled at each assistant turn."""

    def __init__(self) -> None:
        self.pend: dict[str, int] = {}
        self.tool_names: dict[str, str] = {}

    def add(self, bucket: str, chars: int) -> None:
        if chars > 0:
            self.pend[bucket] = self.pend.get(bucket, 0) + chars

    def take(self) -> dict[str, int]:
        out, self.pend = self.pend, {}
        return out


def _read(path) -> list[dict]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


# How many recent message ids to remember. Dedupe only ever matters between
# adjacent records — several transcript lines carry one API message — so a short
# window is enough, and the state is written to disk every turn.
SEEN_WINDOW = 40


def session_split(path, *, records=None) -> Split:
    """Divide one session's window into setup and work.

    Reads a transcript (or a list of records) and returns tokens per category,
    anchored to the exact per-turn totals. Never raises: an unreadable file or a
    malformed record yields a smaller answer, not an exception, because this
    feeds a statusline that must print something on every keystroke.
    """
    recs = records if records is not None else _read(path)
    return _fold(recs, _fresh_state())[0]


def _fresh_state() -> dict:
    return {"offset": 0, "carry": "", "setup": dict.fromkeys(SETUP_KINDS, 0),
            "work": dict.fromkeys(WORK_KINDS, 0), "last_total": 0, "pinned_sys": 0,
            "first_turn": True, "segment_first": True, "segments": 0,
            "turns": 0, "observed_chars": 0, "ratio_tokens": 0, "ratio_chars": 0,
            "seen": []}


def session_split_incremental(path, state: dict | None = None):
    """(Split, state) reading only the bytes added since `state` was made.

    A statusline re-renders on every keystroke and a long session's transcript
    runs to tens of megabytes, so a full parse per render is not available. The
    state carries a byte offset, a partial trailing line, and the running split.
    A file shorter than the offset is not the file we read — start over.
    """
    st = dict(state) if isinstance(state, dict) else _fresh_state()
    for key, default in _fresh_state().items():
        st.setdefault(key, default)
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return _state_split(st), st
    if size < st["offset"]:
        st = _fresh_state()
    if size == st["offset"]:
        return _state_split(st), st
    try:
        with p.open("rb") as fh:
            fh.seek(st["offset"])
            chunk = fh.read()
    except OSError:
        return _state_split(st), st

    # Every byte read is consumed; the unfinished tail is kept as text and
    # prepended next time. Advancing by what we actually read (not by the
    # `size` we sampled before the read) means a transcript appended to between
    # the two never loses or repeats a line.
    st["offset"] += len(chunk)
    text = st["carry"] + chunk.decode("utf-8", "replace")
    lines = text.split("\n")
    # The last element is whatever follows the final newline: either empty, or a
    # record still being written. Either way it is not parseable yet.
    st["carry"] = lines.pop()

    recs = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            recs.append(rec)
    return _fold(recs, st)


def _state_split(st: dict) -> Split:
    split = Split(setup=dict(st["setup"]), work=dict(st["work"]),
                  total=st["last_total"], turns=st["turns"],
                  segments=st["segments"], observed_chars=st["observed_chars"])
    split.ratio = _ratio(st)
    _reconcile(split)
    return split


def _ratio(st: dict) -> float:
    if st["ratio_chars"] < RATIO_MIN_OBSERVED or not st["ratio_chars"]:
        return DEFAULT_RATIO
    return max(RATIO_MIN, min(RATIO_MAX, st["ratio_tokens"] / (st["ratio_chars"] / 4)))


def _fold(recs: list[dict], st: dict):
    acc = _Accumulator()
    split = Split(setup=dict(st["setup"]), work=dict(st["work"]))
    seen_ids = list(st["seen"])
    last_total = st["last_total"]
    pinned_sys = st["pinned_sys"]
    first_turn_of_session = st["first_turn"]
    segment_first_turn = st["segment_first"]
    segments = st["segments"]
    split.turns = st["turns"]
    split.observed_chars = st["observed_chars"]
    # Ratio evidence: tokens the API charged for growth, against the chars we
    # actually watched arrive. Only turns big enough to mean something count.
    ratio_tokens, ratio_chars = st["ratio_tokens"], st["ratio_chars"]

    for rec in recs:
        kind = rec.get("type")

        if kind == "system" and rec.get("subtype") == "compact_boundary":
            # The window is rebuilt: the history is gone and setup is re-injected.
            # So the split describes the CURRENT window — everything attributed
            # so far is discarded — except `sys`, which is re-sent unchanged and
            # was measured once, on the only turn where it could be.
            acc.take()
            split.setup = dict.fromkeys(SETUP_KINDS, 0)
            split.work = dict.fromkeys(WORK_KINDS, 0)
            split.setup["sys"] = pinned_sys
            last_total = 0
            segment_first_turn = True
            continue

        if rec.get("isSidechain"):
            continue

        if kind == "attachment":
            a = rec.get("attachment")
            if not isinstance(a, dict):
                continue
            at = a.get("type")
            if at == "hook_additional_context" and a.get("hookEvent") == "SessionStart":
                # Injected by a plugin at every start and after every compaction:
                # it rides in the prefix like the system prompt does.
                acc.add("sys", _chars(a.get("content")))
            elif at in _SETUP_ATTACHMENTS:
                bucket, fieldname, _shape = _SETUP_ATTACHMENTS[at]
                acc.add(bucket, _chars(a.get(fieldname)))
            elif at in _WORK_ATTACHMENTS:
                acc.add(_WORK_ATTACHMENTS[at], _chars(a.get("content") or a.get("snippet")))
            continue

        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")

        if kind == "user":
            for b in (content or []):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    name = acc.tool_names.get(b.get("tool_use_id"), "")
                    acc.add(_result_bucket(name), _chars(b.get("content")))
                elif b.get("type") == "text":
                    acc.add("chat", _chars(b.get("text")))
            if isinstance(content, str):
                acc.add("chat", len(content))
            continue

        if kind != "assistant":
            continue

        for b in (content or []):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                acc.tool_names[b.get("id")] = b.get("name") or ""
            elif b.get("type") == "text":
                acc.add("chat", _chars(b.get("text")))

        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        total = _usage_total(usage)
        if total <= 0:
            continue                       # synthetic turn (a rate-limit notice)
        mid = msg.get("id") or rec.get("uuid")
        if mid in seen_ids:
            continue                       # several records, one API message
        seen_ids.append(mid)
        del seen_ids[:-SEEN_WINDOW]

        pend = acc.take()
        observed_chars = sum(pend.values())
        split.observed_chars += observed_chars
        split.turns += 1
        if segment_first_turn:
            segments += 1

        ratio = _ratio({"ratio_tokens": ratio_tokens, "ratio_chars": ratio_chars})
        growth = total - last_total

        if segment_first_turn:
            # The system prompt and the built-in tool definitions are never
            # written to the transcript, so on a first turn whatever the exact
            # total does not account for IS them. After a compaction that same
            # unexplained bulk is NOT a second system prompt — it is the MCP
            # schemas ToolSearch loaded on demand earlier, re-sent in the
            # rebuilt prefix. Pinning sys to the session's first segment keeps
            # the number the reader uses to decide what to disable honest.
            #
            # Named sys (prompt snapshot, instructions, start-up hook context) is
            # written again after a compaction, and sometimes only then. It is
            # the same system prompt the first turn already measured — named or
            # not — so it is already inside the pinned figure: it is taken out
            # of the unexplained remainder, and not added to sys a second time.
            named = _distribute(pend, ratio, cap=total)
            if first_turn_of_session:
                remainder = max(0, total - sum(named.values()))
                _merge(split, named)
                _bump(split, "sys", remainder)
                pinned_sys = split.setup["sys"]
                first_turn_of_session = False
            else:
                named.pop("sys", None)
                remainder = max(0, total - sum(named.values()) - split.setup["sys"])
                _merge(split, named)
                _bump(split, "mcp", remainder)
            segment_first_turn = False
        elif growth > 0:
            if observed_chars:
                _merge(split, _distribute(pend, ratio, cap=growth, exact=True))
                if observed_chars >= RATIO_MIN_TURN_CHARS:
                    ratio_tokens += growth
                    ratio_chars += observed_chars
            else:
                # Billed, but nothing visible arrived: extended thinking.
                _bump(split, "other", growth)
        elif growth < 0:
            _shrink_work(split, -growth)

        last_total = total

    st = dict(st, setup=dict(split.setup), work=dict(split.work),
              last_total=last_total, pinned_sys=pinned_sys,
              first_turn=first_turn_of_session, segment_first=segment_first_turn,
              segments=segments, turns=split.turns,
              observed_chars=split.observed_chars,
              ratio_tokens=ratio_tokens, ratio_chars=ratio_chars, seen=seen_ids)
    split.total = last_total
    split.segments = segments
    split.ratio = _ratio(st)
    _reconcile(split)
    return split, st


def _distribute(pend: dict[str, int], ratio: float, *, cap: int,
                exact: bool = False) -> dict[str, int]:
    """Turn pending characters into tokens, never exceeding what was billed.

    `exact` shares the whole of `cap` in proportion — the right thing for a
    turn's growth, which IS the observed content. Without it the estimate is
    kept as an estimate and only clipped, which is what a first turn needs: its
    unexplained remainder is a real quantity, not rounding.
    """
    if not pend:
        return {}
    chars = sum(pend.values())
    if exact:
        out, spent = {}, 0
        items = sorted(pend.items(), key=lambda kv: -kv[1])
        for i, (bucket, c) in enumerate(items):
            # The last bucket takes the remainder so the parts sum to `cap`
            # exactly: a split that loses a token to rounding stops adding up.
            give = cap - spent if i == len(items) - 1 else int(cap * c / chars)
            out[bucket] = give
            spent += give
        return out
    est = {b: int(c / 4 * ratio) for b, c in pend.items()}
    over = sum(est.values())
    if over > cap and over:
        est = {b: int(v * cap / over) for b, v in est.items()}
    return est


def _bump(split: Split, bucket: str, tokens: int) -> None:
    if tokens <= 0:
        return
    if bucket in split.setup:
        split.setup[bucket] += tokens
    else:
        split.work[bucket] = split.work.get(bucket, 0) + tokens


def _merge(split: Split, parts: dict[str, int]) -> None:
    for bucket, tokens in parts.items():
        _bump(split, bucket, tokens)


def _shrink_work(split: Split, tokens: int) -> None:
    """A window that got smaller lost history, never setup.

    Setup is re-sent verbatim every turn, so it cannot be what went away; a
    tool result that was cleared can be. Taken proportionally across work.
    """
    pool = sum(split.work.values())
    if pool <= 0:
        return
    take = min(tokens, pool)
    for bucket in list(split.work):
        split.work[bucket] -= int(take * split.work[bucket] / pool)
    drift = sum(split.work.values()) - (pool - take)
    if drift:
        largest = max(split.work, key=lambda b: split.work[b])
        split.work[largest] = max(0, split.work[largest] - drift)


def _reconcile(split: Split) -> None:
    """Make the parts sum to the exact total, and say where the gap went.

    Rounding, a clipped estimate, or a shrink that outran what we had attributed
    all leave the parts a little off the one number that is not an estimate. The
    difference belongs in `other`, which is already the honest bucket, rather
    than being spread around to hide it.
    """
    if split.total <= 0:
        for d in (split.setup, split.work):
            for k in d:
                d[k] = 0
        return
    gap = split.total - (split.setup_total + split.work_total)
    if gap > 0:
        split.work["other"] += gap
    elif gap < 0:
        _shrink_work(split, -gap)
        gap = split.total - (split.setup_total + split.work_total)
        if gap < 0:                          # work was already empty: clip setup
            largest = max(split.setup, key=lambda b: split.setup[b])
            split.setup[largest] = max(0, split.setup[largest] + gap)


# --- the cache the statusline reads -----------------------------------------
#
# The statusline runs under whatever `python3` is first on PATH, which usually
# cannot import tokendog, and it re-renders on every keystroke. So it never
# computes the split: a tokendog-capable hook writes this small file at the end
# of each turn and the statusline just reads it. Same arrangement as the floor
# cache, and the reason both surfaces agree by construction.

# The session id arrives in a hook payload and becomes a filename. Anything but
# this shape is refused rather than sanitised — a rejected write is a missing
# segment, a mis-sanitised one is a write somewhere else.
_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def split_cache_dir(home=None) -> Path:
    from .config import tokendog_home
    return (Path(home) if home is not None else tokendog_home()) / "split"


def _cache_path(session_id: str, home=None) -> Path | None:
    if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
        return None
    return split_cache_dir(home) / f"{session_id}.json"


def refresh_cache(session_id: str, transcript_path, *, home=None) -> dict | None:
    """Update one session's cached split from the bytes added since last time.

    Returns the split as a dict, or None if the id is not usable. Never raises:
    it runs inside a hook, and a hook that throws takes the session with it.
    """
    path = _cache_path(session_id, home)
    if path is None:
        return None
    state = None
    try:
        state = json.loads(path.read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError):
        pass
    try:
        split, state = session_split_incremental(transcript_path, state)
    except Exception:
        return None
    payload = split.as_dict()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"split": payload, "state": state},
                                  separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)                 # a half-written cache must never be read
        _prune(split_cache_dir(home))
    except OSError:
        pass
    return payload


def read_cached_split(session_id: str, *, home=None) -> dict | None:
    path = _cache_path(session_id, home)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["split"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _prune(directory: Path, keep: int = 60) -> None:
    """One file per session, forever, is a slow leak on a laptop."""
    try:
        files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for stale in files[:-keep]:
        try:
            stale.unlink()
        except OSError:
            pass
