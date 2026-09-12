from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

from .bands import LARGE_CONTEXT_THRESHOLD
from .limit_resume import is_reset
from .pricing import (CACHE_READ_MULTIPLIER, CACHE_WRITE_1H_MULTIPLIER,
                      CACHE_WRITE_5M_MULTIPLIER)

# Session hygiene: was this session managed, or did it simply accumulate?
#
# Bands say how big the windows were. Resumes say a window was carried past an
# obvious moment to reset it. This asks the plainer question underneath both:
# over its whole life, did anyone ever reset this session — and what did not
# doing so cost?
#
# THE EXCESS. A turn above the threshold did not have to carry everything it
# carried: had the session been reset at the threshold, that turn would have
# carried at most the threshold. So the avoidable part of one turn is
# `occupancy - threshold`, and a session's excess is that summed over its turns.
# This is the most defensible avoidable-cost measure in the tool: it needs no
# counterfactual about what a reset would have re-read, because it only ever
# counts tokens that were carried ABOVE a line the session could have held.
#
# MANAGED vs NOT. A session that grew large and reset repeatedly was busy. A
# session that grew large and never reset once was unmanaged, and they deserve
# different advice — so resets are counted, reusing the definition that
# `limit_resume` already fixed rather than inventing a second one.
#
# NO LIVE WINDOW. Age and idleness only mean something for a session a reader
# could actually go and reset. Two kinds cannot be:
#   - a headless run, which has already exited;
#   - a SUBAGENT transcript, whose context ends when the subagent finishes.
# Telling someone to close either is noise. Both are still measured for excess,
# because what they carried is real regardless of who could close it — a
# subagent handed 400k cost that, and the fix is the delegation, not a reset.
#
# Subagent transcripts are identified by their filename prefix, which is also
# why roll-ups key on transcript rather than session id: an `agent-*` transcript
# carries its PARENT's sessionId, so keying on session would merge a hundred
# subagent contexts into the one row of the session that spawned them.

OCCUPANCY_WARN = LARGE_CONTEXT_THRESHOLD    # 200k — imported, never restated
OCCUPANCY_ALARM = 400_000

AGE_LONG_LIVED_H = 48.0     # alive across days: the task moved on, the session did not
IDLE_STALE_H = 12.0         # untouched this long and it is not being come back to today
STALE_MIN_CONTEXT = 150_000  # ...and still holding this much, so reopening it is expensive

SEVERITIES = ("crit", "ser", "warn", "ok")

# Subagent transcripts are named `agent-<id>.jsonl` by the runtime.
SUBAGENT_PREFIX = "agent-"


def is_subagent(transcript_id: str | None) -> bool:
    """True for a subagent's own transcript, which no reader can close."""
    return bool(transcript_id) and str(transcript_id).startswith(SUBAGENT_PREFIX)


BUCKETS = ("cache_read", "write_5m", "write_1h", "write_other", "fresh", "output")


def buckets_of(event) -> dict:
    """The five token buckets one turn was made of, kept apart because they bill
    at five different rates.

    `input` alone cannot be priced: 600k read from cache costs a twentieth of
    600k written to a 1-hour cache. A session whose whole window is cache reads
    and one whose window is rewritten every turn look identical in a total and
    differ twentyfold on the bill, which is exactly the distinction a reader
    chasing a limit needs.

    `write_other` is cache creation whose TTL the record did not report. It is
    billed at the 5-minute rate, which is the runtime's default — the
    conservative choice, since assuming 1h would inflate the figure.
    """
    read = int(getattr(event, "cache_read_tokens", 0) or 0)
    total_write = int(getattr(event, "cache_creation_tokens", 0) or 0)
    w5m = int(getattr(event, "cache_creation_5m_tokens", 0) or 0)
    w1h = int(getattr(event, "cache_creation_1h_tokens", 0) or 0)
    return {
        "cache_read": read,
        "write_5m": w5m,
        "write_1h": w1h,
        "write_other": max(0, total_write - w5m - w1h),
        "fresh": int(getattr(event, "input_tokens", 0) or 0),
        "output": int(getattr(event, "output_tokens", 0) or 0),
    }


def weighted_input(buckets: dict) -> int:
    """Input-side tokens at their billing weights, in input-token equivalents.

    Output is excluded for the same reason it is excluded from occupancy: it
    came back rather than being carried, so including it would make this
    incomparable with the `input` column beside it.
    """
    return int(
        buckets.get("cache_read", 0) * CACHE_READ_MULTIPLIER
        + buckets.get("write_5m", 0) * CACHE_WRITE_5M_MULTIPLIER
        + buckets.get("write_1h", 0) * CACHE_WRITE_1H_MULTIPLIER
        + buckets.get("write_other", 0) * CACHE_WRITE_5M_MULTIPLIER
        + buckets.get("fresh", 0)
    )


@dataclass
class Session:
    """One session's hygiene: how it grew, whether it was managed, what that cost."""

    transcript: str
    session: str
    project: str | None
    headless: bool
    subagent: bool
    kind: str
    model: str | None
    turns: int
    input: int
    first_context: int
    last_context: int
    avg_context: int
    peak_context: int
    span_hours: float
    idle_hours: float
    resets: int
    over_threshold_turns: int
    excess_tokens: int
    # How many separate contexts this one transcript has held, and the stats of
    # the one still live. Lifetime figures describe what the session COST;
    # `current` describes what it IS.
    segments: int
    current: dict | None
    # What the scoped turns were made of, and what they weigh once the five
    # billing rates are applied. `input` says how much was carried;
    # `weighted_input` says how much of it was expensive.
    buckets: dict
    weighted_input: int
    # Input tokens per minute across the scoped turns. The figure that separates
    # a runaway from a grinder: two sessions can carry the same total, one over
    # twenty minutes and one over nine hours, and only the first is why a
    # five-hour limit just went. None when the slice is a single turn, which has
    # no elapsed time to divide by.
    burn_per_min: float | None
    # Subagent transcripts spawned under this session, rolled up. Set on the
    # PARENT row only — the subagents keep their own rows, because their
    # contexts are separate and their excess is theirs. Without this a fan-out
    # reads as a dozen innocent small sessions and the delegation that caused
    # them is invisible.
    subagents: int
    subagent_input: int
    parent: str | None
    severity: str
    action: str

    def as_dict(self) -> dict:
        return asdict(self)


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def excess(contexts: Iterable[int], *, threshold: int = OCCUPANCY_WARN) -> tuple[int, int]:
    """(turns over the threshold, tokens carried above it).

    Only the part above the line is counted. A session held entirely under the
    threshold has no excess, which is the point — the measure is silent about
    sessions doing nothing wrong.
    """
    turns = 0
    tokens = 0
    for ctx in contexts:
        over = int(ctx) - threshold
        if over > 0:
            turns += 1
            tokens += over
    return turns, tokens


def count_resets(contexts: list[int]) -> int:
    """How many times occupancy collapsed from a large window."""
    return sum(1 for a, b in zip(contexts, contexts[1:]) if is_reset(a, b))


def segment(turns: list[tuple]) -> list[dict]:
    """Split a transcript's turns into the separate CONTEXTS it actually holds.

    A reset does not end a transcript — the file keeps growing — so one file can
    hold many contexts. Measured on a real corpus: one transcript carried ELEVEN,
    climbing 43k -> 999k, being cleared, and climbing again, ten times over.

    That makes a lifetime average meaningless as a description of the session's
    present shape: it blends every context the file ever held. So the turns are
    cut at each reset, and the LAST segment is the one a reader can still act on
    — everything before it is history that has already been discarded.

    `turns` is (timestamp, occupancy) in order. Returns one dict per segment.
    """
    out: list[dict] = []
    current: list[tuple] = []
    for i, row in enumerate(turns):
        if i and is_reset(turns[i - 1][1], row[1]) and current:
            out.append(_summarise(current))
            current = []
        current.append(row)
    if current:
        out.append(_summarise(current))
    return out


def _summarise(rows: list[tuple]) -> dict:
    ctx = [c for _, c in rows]
    return {
        "turns": len(rows),
        "input": sum(ctx),
        "first_context": ctx[0],
        "last_context": ctx[-1],
        "avg_context": sum(ctx) // len(ctx),
        "peak_context": max(ctx),
        "started": rows[0][0].astimezone().isoformat(timespec="minutes"),
        "ended": rows[-1][0].astimezone().isoformat(timespec="minutes"),
        "hours": round((rows[-1][0] - rows[0][0]).total_seconds() / 3600.0, 1),
    }


def classify(*, avg_context: int, last_context: int, span_hours: float,
             idle_hours: float, resets: int, no_live_window: bool = False,
             kind: str = "interactive") -> tuple[str, str]:
    """(severity, action) for one session.

    Ordered by what the reader should do FIRST, not by how bad it looks: a
    loaded session sitting idle is the cheapest thing to fix and the most
    expensive thing to ignore, so it outranks a merely old one.

    `no_live_window` suppresses every age- and idle-based verdict, because there
    is nothing left to reset — see the note at the top of this module. `kind`
    then decides which fix to name instead.
    """
    if no_live_window:
        if avg_context < OCCUPANCY_WARN:
            return "ok", "Healthy shape"
        if kind == "subagent":
            return "warn", "Scope the subagent"
        return "warn", "Pre-load shared context"
    if idle_hours >= IDLE_STALE_H and last_context >= STALE_MIN_CONTEXT:
        return "crit", "Close it now"
    if last_context >= OCCUPANCY_ALARM:
        return "crit", "Clear and restart"
    # Age is a proxy for accumulation, and only that. A session compacted back
    # down is billed on what its window holds now, whatever its birthday; the
    # occupancy rules below judge it. Without a reset, days of topics are
    # still in the window and retiring is the fix.
    if span_hours >= AGE_LONG_LIVED_H and not resets:
        return "ser", "Retire the session"
    if avg_context >= OCCUPANCY_WARN and not resets:
        return "warn", "Never reset — compact at 120K"
    if avg_context >= OCCUPANCY_WARN:
        return "warn", "Compact at 120K"
    # Falls through to healthy: a session that has been reset and is now small
    # is in good shape, whatever its lifetime figures say.
    return "ok", "Healthy shape"


def session_findings(transcript: str, project: str | None, *, contexts: list[int],
                     no_live_window: bool, span_hours: float, idle_hours: float,
                     last_context: int, avg_context: int, resets: int,
                     excess_tokens: int | None = None) -> list[dict]:
    """The findings one session raises, so every surface raises the same ones.

    Both `hygiene_summary` and the dashboard's view-model call this. They used
    to each build `long-lived` and `stale-open` themselves, and the two copies
    drifted: one grew the guard that keeps age and idleness away from a run with
    no live window, and the other did not — so a subagent transcript was told it
    was stale on one surface and not the other.
    """
    out: list[dict] = []
    if excess_tokens is None:
        _, excess_tokens = excess(contexts)
    # Same rule as `classify`: age only matters when nothing was ever reset.
    if not no_live_window and span_hours >= AGE_LONG_LIVED_H and not resets:
        out.append({"kind": "long-lived", "session": transcript[:8],
                    "project": project, "span_hours": span_hours,
                    "avg_context": avg_context, "resets": resets})
    if (not no_live_window and idle_hours >= IDLE_STALE_H
            and last_context >= STALE_MIN_CONTEXT):
        out.append({"kind": "stale-open", "session": transcript[:8],
                    "project": project, "idle_hours": idle_hours,
                    "last_context": last_context})
    if not resets and contexts and max(contexts) >= OCCUPANCY_ALARM:
        out.append({"kind": "unmanaged-drift", "session": transcript[:8],
                    "project": project, "first_context": contexts[0],
                    "peak_context": max(contexts), "excess_tokens": excess_tokens})
    return out


def _roll_up_subagents(sessions: list) -> None:
    """Credit each subagent's tokens to the session that spawned it, in place.

    A subagent transcript carries its PARENT's sessionId — the same fact that
    makes keying the rows on session id wrong is what makes this attribution
    possible. The subagent rows are left exactly as they are: their contexts
    were separate and their excess is their own. This only adds, to the parent,
    the count and size of what it delegated, so a fan-out stops reading as a
    dozen unrelated small sessions.

    A resumed session can own several transcripts, of which only the one NAMED
    by the session id is the parent; crediting every one of them would report
    the same fan-out several times over. Where no transcript carries that name —
    a transcript that was rotated, say — the busiest non-subagent row takes it,
    which keeps the total right even when the exact parent cannot be named.
    """
    kids: dict[str, list] = defaultdict(list)
    for s in sessions:
        if s.subagent:
            kids[s.session].append(s)
    if not kids:
        return
    for session_id, group in kids.items():
        candidates = [s for s in sessions
                      if not s.subagent and s.session == session_id]
        if not candidates:
            # The parent has no row: inside a narrow window a session can sit
            # idle while the subagents it spawned burn. Name it on the children
            # anyway — a fan-out whose parent is invisible is the case the
            # reader most needs pointed at, and dropping the link entirely
            # leaves a dozen orphan rows with nothing tying them together.
            for kid in group:
                kid.parent = session_id[:8]
            continue
        named = [s for s in candidates if s.transcript == session_id]
        parent = named[0] if named else max(candidates, key=lambda s: s.turns)
        parent.subagents = len(group)
        parent.subagent_input = sum(k.input for k in group)
        for kid in group:
            kid.parent = parent.transcript


def hygiene_summary(events: Iterable, *, now=None, since=None, window=None,
                    threshold: int = OCCUPANCY_WARN) -> dict:
    """Assess every session in the stream.

    `window` (a `window.Window`) scopes which TURNS count; `since` is the older
    one-sided form of the same thing and still works. Scoping happens after the
    whole life of each session has been collected, on purpose: what a session
    SPENT belongs to the window, but how long it has been open and how long it
    has been idle are facts about the session itself, and reporting a
    three-day-old session as "open for 16 minutes" because that is all the
    window saw would be worse than useless — it would hide exactly the sessions
    this report exists to find.
    """
    now = now or datetime.now(timezone.utc)
    per: dict[str, dict] = defaultdict(
        lambda: {"turns": [], "project": None, "session": None,
                 "headless_turns": 0, "models": defaultdict(int)})

    for e in events:
        if not getattr(e, "is_turn", False):
            continue
        when = _parse(getattr(e, "ts", None))
        if when is None:
            continue
        key = getattr(e, "transcript_id", None) or getattr(e, "session_id", None) or "unknown"
        rec = per[key]
        rec["turns"].append((when, int(e.context_tokens or 0), buckets_of(e)))
        rec["project"] = rec["project"] or getattr(e, "project", None)
        rec["session"] = rec["session"] or getattr(e, "session_id", None)
        # Counted, not OR'd. A long interactive session that once shelled out to
        # a print-mode run carries a handful of sdk-cli records among thousands
        # of cli ones, and a sticky OR let those few decide: the session was
        # reported headless, and headless suppresses every age and idle verdict,
        # so the one session actually worth closing was told to pre-load its
        # context instead. Observed at 87 sdk-cli records against 2,025 cli.
        if getattr(e, "is_headless", False):
            rec["headless_turns"] += 1
        if getattr(e, "model", None):
            rec["models"][e.model] += 1

    def in_scope(when) -> bool:
        if window is not None:
            return window.contains(when)
        return since is None or when >= since

    sessions: list[Session] = []
    findings: list[dict] = []
    for key, rec in per.items():
        rows = sorted(rec["turns"], key=lambda r: r[0])
        ordered = [(w, c) for w, c, _ in rows]
        scoped_rows = [r for r in rows if in_scope(r[0])]
        scoped = [(w, c) for w, c, _ in scoped_rows]
        if not scoped:
            continue
        contexts = [c for _, c in scoped]
        totals = {b: 0 for b in BUCKETS}
        for _, _, buckets in scoped_rows:
            for b in BUCKETS:
                totals[b] += buckets.get(b, 0)
        elapsed_min = (scoped[-1][0] - scoped[0][0]).total_seconds() / 60.0
        burn = round(sum(contexts) / elapsed_min, 1) if elapsed_min > 0 else None
        # Span and idleness describe the whole life of the session, not just the
        # scoped slice: a session that opened last week is old regardless of the
        # window a report happens to cover.
        span_h = round((ordered[-1][0] - ordered[0][0]).total_seconds() / 3600.0, 1)
        idle_h = round((now - ordered[-1][0]).total_seconds() / 3600.0, 1)
        over_turns, excess_tokens = excess(contexts, threshold=threshold)
        resets = count_resets([c for _, c in ordered])
        segs = segment(ordered)
        live = segs[-1] if segs else None
        headless = rec["headless_turns"] * 2 > len(rec["turns"])
        subagent = is_subagent(key)
        kind = "subagent" if subagent else ("headless" if headless else "interactive")
        avg_context = sum(contexts) // len(contexts)
        last_context = ordered[-1][1]
        # The verdict is about the CURRENT context, not the lifetime average:
        # a session cleared an hour ago is healthy now even if it once carried
        # 999k, and telling the reader to compact it would be wrong.
        severity, action = classify(
            avg_context=(live or {}).get("avg_context", avg_context),
            last_context=last_context,
            span_hours=span_h, idle_hours=idle_h, resets=resets,
            no_live_window=headless or subagent, kind=kind)
        sessions.append(Session(
            transcript=key, session=rec["session"] or key, project=rec["project"],
            headless=headless, subagent=subagent, kind=kind,
            model=max(rec["models"], key=rec["models"].get) if rec["models"] else None,
            turns=len(scoped), input=sum(contexts),
            first_context=ordered[0][1], last_context=last_context,
            avg_context=avg_context, peak_context=max(contexts),
            span_hours=span_h, idle_hours=idle_h, resets=resets,
            over_threshold_turns=over_turns, excess_tokens=excess_tokens,
            segments=len(segs), current=live,
            buckets=totals, weighted_input=weighted_input(totals),
            burn_per_min=burn, subagents=0, subagent_input=0,
            parent=None, severity=severity, action=action))

        findings.extend(session_findings(
            key, rec["project"], contexts=[c for _, c in ordered],
            no_live_window=headless or subagent, span_hours=span_h,
            idle_hours=idle_h, last_context=last_context,
            avg_context=avg_context, resets=resets, excess_tokens=excess_tokens))

    _roll_up_subagents(sessions)

    # All-time, rank by what is worth fixing; inside a window, by what was
    # actually spent. A session that carried 400k for twenty minutes has no
    # excess if it stayed under the line, and ranking a named window by excess
    # buries exactly the row the reader opened the window to find.
    if window is not None:
        sessions.sort(key=lambda s: (-s.weighted_input, -s.input))
    else:
        sessions.sort(key=lambda s: -s.excess_tokens)
    counts = defaultdict(int)
    for s in sessions:
        counts[s.severity] += 1

    return {
        "sessions": [s.as_dict() for s in sessions],
        "findings": findings,
        "severity_counts": {k: counts[k] for k in SEVERITIES if counts[k]},
        "totals": {
            "sessions": len(sessions),
            "excess_tokens": sum(s.excess_tokens for s in sessions),
            "never_reset": sum(1 for s in sessions if not s.resets),
            "needing_action": sum(1 for s in sessions if s.severity != "ok"),
            "input": sum(s.input for s in sessions),
            "weighted_input": sum(s.weighted_input for s in sessions),
            "subagents": sum(1 for s in sessions if s.subagent),
            "buckets": {b: sum(s.buckets.get(b, 0) for s in sessions)
                        for b in BUCKETS},
        },
        "window": window.label if window is not None else None,
        "window_minutes": window.minutes if window is not None else None,
        "turns": sum(s.turns for s in sessions),
        "thresholds": {
            "occupancy_warn": threshold,
            "occupancy_alarm": OCCUPANCY_ALARM,
            "age_long_lived_hours": AGE_LONG_LIVED_H,
            "idle_stale_hours": IDLE_STALE_H,
            "stale_min_context": STALE_MIN_CONTEXT,
        },
    }


def hygiene_report_data(transcript_root=None, project=None, *, window=None) -> dict:
    """Read transcripts and summarise, mirroring `report.band_report_data`.

    The window is passed DOWN rather than used to filter the stream here,
    because this report needs each session's whole life to judge its age even
    when only part of it is in scope.
    """
    from itertools import chain
    from .sink import read_events
    from .transcripts import read_transcripts
    events = chain(read_transcripts(transcript_root), read_events())
    if project:
        events = (e for e in events if getattr(e, "project", None) == project)
    data = hygiene_summary(events, window=window)
    data["project"] = project
    return data
