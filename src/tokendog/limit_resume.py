from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

from .bands import LARGE_CONTEXT_THRESHOLD

# Resume-at-the-wall: the session paused, then carried on in a window that was
# already large instead of being reset.
#
# This is the most expensive habit the band data implies but does not name. A
# band tells you a turn was costly; it cannot tell you the turn was avoidable.
# The shape here can: a pause long enough to be a rate-limit wait or a walk
# away is the natural moment to reset, and a turn that resumes at 900k has
# skipped it — every turn after that one re-reads 900k that a reset would have
# discarded.
#
# WHAT THIS DOES NOT CLAIM. A transcript records no reason for a pause, so this
# never asserts a rate limit was hit. It names a shape — long gap, large window
# — and leaves the cause to the reader. Nor is the reported cost a saving: it is
# what a reset at that moment could at most have avoided, and a real reset
# re-reads some of the same material. Ceiling, not forecast.

# A pause at least this long is a decision point: long enough that the work was
# interrupted rather than continuous.
GAP_MINUTES = 30.0

# ...and a window at least this large is one worth resetting. Set at twice the
# large-context threshold the band tables already use, so a session merely
# working at a normal size is never flagged for pausing for lunch.
MIN_CONTEXT = LARGE_CONTEXT_THRESHOLD * 2

# A reset: occupancy collapsing from a large window. A conversation only grows
# turn to turn, so a fall this steep means content left the prompt — a
# compaction or a manual clear, counted the same because the transcript cannot
# distinguish them and the cost effect is identical.
RESET_DROP_SHARE = 0.4
RESET_FLOOR = 100_000


@dataclass
class Resume:
    """One resume-at-the-wall, with what carrying that window went on to cost."""

    transcript: str
    session: str
    project: str | None
    at: str
    context: int
    gap_minutes: float
    turns_after: int
    carried_tokens: int
    ended_with_reset: bool
    model: str | None

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


def is_reset(previous_context: int, context: int) -> bool:
    """True when occupancy collapsed from a large window between two turns."""
    return (previous_context >= RESET_FLOOR
            and context <= previous_context * RESET_DROP_SHARE)


def find_resumes(turns: list[tuple[datetime, int]], *,
                 gap_minutes: float = GAP_MINUTES,
                 min_context: int = MIN_CONTEXT) -> list[dict]:
    """Locate resumes in one transcript's turns, ordered oldest first.

    `turns` is (timestamp, occupancy) per metered turn. Returns dicts rather
    than Resume objects because the per-transcript identity is added by the
    caller, which is the only thing that knows it.

    Cost model: `turns_after` counts the turns that ran after this resume and
    before the NEXT DECISION POINT — the next reset, or the next resume, or the
    end of the transcript. Stopping at the next resume is what keeps the
    attribution non-overlapping: each turn is charged to exactly one resume, so
    the total is bounded by the tokens actually carried. Counting to the next
    reset instead let consecutive resumes each claim the same turns, which
    summed to more tokens than the corpus contains.

    `carried_tokens` is then that count times the occupancy at the resume — the
    ceiling on what resetting at that moment could have avoided.
    """
    out: list[dict] = []
    marks: list[int] = []
    for i in range(1, len(turns)):
        prev_t, prev_ctx = turns[i - 1]
        t, ctx = turns[i]
        gap = (t - prev_t).total_seconds() / 60.0
        if gap < gap_minutes or ctx < min_context:
            continue
        # A resume INTO a reset window is the good case, not the bad one.
        if is_reset(prev_ctx, ctx):
            continue
        marks.append(i)

    for n, i in enumerate(marks):
        stop = marks[n + 1] if n + 1 < len(marks) else len(turns)
        t, ctx = turns[i]
        gap = (t - turns[i - 1][0]).total_seconds() / 60.0
        turns_after = 0
        ended_with_reset = False
        for j in range(i + 1, stop):
            if is_reset(turns[j - 1][1], turns[j][1]):
                ended_with_reset = True
                break
            turns_after += 1
        out.append({
            "at": t.astimezone().isoformat(timespec="minutes"),
            "context": ctx,
            "gap_minutes": round(gap, 1),
            "turns_after": turns_after,
            "carried_tokens": ctx * turns_after,
            "ended_with_reset": ended_with_reset,
        })
    return out


def _escalating(contexts: list[int]) -> bool:
    """True when a session's resumes end higher than they started.

    The compounding signal: not resetting raises the floor for the next resume.

    Measured as a NET RISE between the first and last resume, which is exactly
    what the reported `first → last` pair shows, rather than strict step-by-step
    monotonicity. A session with dozens of resumes always dips somewhere — a
    compaction partway through, a smaller task in between — so requiring every
    step to rise reported "no" for sessions that plainly climbed from 525k to
    868k. Endpoints are the honest summary of a trend at this scale.
    """
    return len(contexts) > 1 and contexts[-1] > contexts[0]


def resume_summary(events: Iterable, *, gap_minutes: float = GAP_MINUTES,
                   min_context: int = MIN_CONTEXT) -> dict:
    """Every resume-at-the-wall across a stream of metered turns."""
    per: dict[str, dict] = defaultdict(
        lambda: {"turns": [], "session": None, "project": None, "models": defaultdict(int)})
    for e in events:
        if not getattr(e, "is_turn", False):
            continue
        when = _parse(getattr(e, "ts", None))
        if when is None:
            continue
        key = getattr(e, "transcript_id", None) or getattr(e, "session_id", None) or "unknown"
        rec = per[key]
        rec["turns"].append((when, int(e.context_tokens or 0)))
        rec["session"] = rec["session"] or getattr(e, "session_id", None)
        rec["project"] = rec["project"] or getattr(e, "project", None)
        if getattr(e, "model", None):
            rec["models"][e.model] += 1

    resumes: list[Resume] = []
    for key, rec in per.items():
        ordered = sorted(rec["turns"], key=lambda r: r[0])
        model = max(rec["models"], key=rec["models"].get) if rec["models"] else None
        for found in find_resumes(ordered, gap_minutes=gap_minutes,
                                  min_context=min_context):
            resumes.append(Resume(transcript=key, session=rec["session"] or key,
                                  project=rec["project"], model=model, **found))

    resumes.sort(key=lambda r: r.at)

    by_session: dict[str, list[Resume]] = defaultdict(list)
    for r in resumes:
        by_session[r.transcript].append(r)

    sessions = []
    for key, group in by_session.items():
        contexts = [r.context for r in group]
        sessions.append({
            "transcript": key,
            "session": group[0].session,
            "project": group[0].project,
            "count": len(group),
            "escalating": _escalating(contexts),
            "first_context": contexts[0],
            "last_context": contexts[-1],
            "peak_context": max(contexts),
            "carried_tokens": sum(r.carried_tokens for r in group),
            "reset_after": sum(1 for r in group if r.ended_with_reset),
        })
    sessions.sort(key=lambda s: -s["carried_tokens"])

    return {
        "resumes": [r.as_dict() for r in resumes],
        "sessions": sessions,
        "totals": {
            "count": len(resumes),
            "sessions": len(sessions),
            "carried_tokens": sum(r.carried_tokens for r in resumes),
            "escalating_sessions": sum(1 for s in sessions if s["escalating"]),
            "never_reset": sum(1 for r in resumes if not r.ended_with_reset),
        },
        "thresholds": {
            "gap_minutes": gap_minutes,
            "min_context": min_context,
            "reset_drop_share": RESET_DROP_SHARE,
            "reset_floor": RESET_FLOOR,
        },
    }


def resume_report_data(transcript_root=None, project=None, *, window=None) -> dict:
    """Read transcripts and summarise, mirroring `report.band_report_data`."""
    from itertools import chain
    from .sink import read_events
    from .transcripts import read_transcripts
    from .window import scoped
    events = chain(read_transcripts(transcript_root), read_events())
    if project:
        events = (e for e in events if getattr(e, "project", None) == project)
    events = scoped(events, window)
    data = resume_summary(events)
    data["project"] = project
    data["window"] = window.label if window is not None else None
    return data
