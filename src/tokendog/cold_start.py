from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

from .event import SOURCE_HOOK

# Cold-start duplication: several non-interactive runs against one project,
# each starting from an empty window and re-discovering the same material.
#
# An interactive session's problem is that it carries too much. A headless run
# has the opposite problem — it carries nothing, every time. Run it eleven times
# over one repository and you pay the cost of finding your way around that
# repository eleven times, for knowledge the previous run already had. Neither
# bands nor resumes can see this: every individual run looks modest, and the
# waste only exists in the comparison between them.
#
# THE RAMP. A run's discovery is the climb at its start: occupancy rising
# steeply as files are read, before it settles into doing the actual work. The
# ramp is measured as the turns before occupancy first reaches half that run's
# own peak — self-scaling, so it needs no magic turn count and adapts to a run
# that reads two files or two hundred.
#
# WHAT IS ATTRIBUTED. One run in a group genuinely has to discover; the others
# need not have. So the duplicated cost is the whole group's ramp minus the
# cheapest single ramp in it. That is conservative by construction, and it goes
# to zero for a group of one — which is why a lone run is never flagged.
#
# WHAT IS NOT CLAIMED. Occupancy cannot prove two runs read the SAME files, only
# that both paid to get up to speed. Where hook telemetry exists it names the
# files that more than one run actually touched, and that is reported as
# corroboration; without it the ramp remains circumstantial, and the report says
# so rather than overstating.

MIN_RUNS = 2               # one run has to discover; duplication needs a second
RAMP_PEAK_SHARE = 0.5      # the ramp ends when occupancy first reaches half the peak
MIN_RAMP_TOKENS = 50_000   # below this the discovery was trivial and not worth naming


@dataclass
class Run:
    """One non-interactive run and what getting up to speed cost it."""

    transcript: str
    session: str
    project: str | None
    entrypoint: str | None
    started: str
    turns: int
    input: int
    peak: int
    ramp_turns: int
    ramp_tokens: int

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


def ramp(contexts: list[int], *, peak_share: float = RAMP_PEAK_SHARE) -> tuple[int, int]:
    """(turns, tokens) spent before occupancy first reached `peak_share` of peak.

    The turn that crosses the threshold is part of the ramp — it is the read
    that got the run up to speed, not the first turn of real work.
    """
    if not contexts:
        return 0, 0
    target = max(contexts) * peak_share
    tokens = 0
    for i, ctx in enumerate(contexts):
        tokens += ctx
        if ctx >= target:
            return i + 1, tokens
    return len(contexts), tokens


def _shared_files(events_by_session: dict[str, set[str]]) -> list[dict]:
    """Files touched by more than one run, from hook telemetry when present."""
    seen: dict[str, set[str]] = defaultdict(set)
    for session, files in events_by_session.items():
        for f in files:
            seen[f].add(session)
    shared = [{"file": f, "runs": len(s)} for f, s in seen.items() if len(s) > 1]
    shared.sort(key=lambda r: (-r["runs"], r["file"]))
    return shared


def cold_start_summary(events: Iterable, *, min_runs: int = MIN_RUNS,
                       min_ramp_tokens: int = MIN_RAMP_TOKENS) -> dict:
    """Group non-interactive runs by project and price their repeated discovery."""
    per: dict[str, dict] = defaultdict(
        lambda: {"contexts": [], "project": None, "session": None,
                 "entrypoint": None, "started": None, "turns": 0, "input": 0})
    files_by_session: dict[str, set[str]] = defaultdict(set)

    for e in events:
        # Hook events are not turns, but they are the only place a file name
        # appears — so they are read for corroboration before turns are filtered.
        if getattr(e, "source", None) == SOURCE_HOOK and getattr(e, "file", None):
            files_by_session[getattr(e, "session_id", "") or ""].add(e.file)
        if not getattr(e, "is_turn", False):
            continue
        if not getattr(e, "is_headless", False):
            continue
        key = getattr(e, "transcript_id", None) or getattr(e, "session_id", None) or "unknown"
        rec = per[key]
        ctx = int(e.context_tokens or 0)
        rec["contexts"].append((_parse(getattr(e, "ts", None)), ctx))
        rec["turns"] += 1
        rec["input"] += ctx
        rec["project"] = rec["project"] or getattr(e, "project", None)
        rec["session"] = rec["session"] or getattr(e, "session_id", None)
        rec["entrypoint"] = rec["entrypoint"] or getattr(e, "entrypoint", None)

    runs: list[Run] = []
    for key, rec in per.items():
        # A transcript with no occupancy at all is a run that started and
        # exited — one usage block of zeroes. It is not a run that discovered
        # anything, and leaving it in let it absorb the "one run had to
        # discover" credit for free, which reported the whole group ramp as
        # duplicated.
        if rec["input"] <= 0:
            continue
        ordered = [c for t, c in sorted(rec["contexts"],
                                        key=lambda r: (r[0] is None, r[0]))]
        first = next((t for t, _ in sorted(rec["contexts"],
                                           key=lambda r: (r[0] is None, r[0]))
                      if t is not None), None)
        ramp_turns, ramp_tokens = ramp(ordered)
        runs.append(Run(
            transcript=key, session=rec["session"] or key, project=rec["project"],
            entrypoint=rec["entrypoint"],
            started=first.astimezone().isoformat(timespec="minutes") if first else "",
            turns=rec["turns"], input=rec["input"], peak=max(ordered) if ordered else 0,
            ramp_turns=ramp_turns, ramp_tokens=ramp_tokens))

    by_project: dict[str, list[Run]] = defaultdict(list)
    for r in runs:
        by_project[r.project or "unknown"].append(r)

    groups = []
    for project, group in by_project.items():
        if len(group) < min_runs:
            continue
        ramps = [r.ramp_tokens for r in group]
        duplicated = sum(ramps) - min(ramps)
        if duplicated < min_ramp_tokens:
            continue
        group.sort(key=lambda r: -r.ramp_tokens)
        sessions = {r.session for r in group}
        shared = _shared_files({s: files_by_session[s] for s in sessions
                                if files_by_session.get(s)})
        groups.append({
            "project": project,
            "runs": len(group),
            "turns": sum(r.turns for r in group),
            "input": sum(r.input for r in group),
            "ramp_tokens": sum(ramps),
            "duplicated_tokens": duplicated,
            "cheapest_ramp": min(ramps),
            "mean_ramp_turns": round(sum(r.ramp_turns for r in group) / len(group), 1),
            "shared_files": shared[:10],
            "shared_file_count": len(shared),
            "runs_detail": [r.as_dict() for r in group],
        })
    groups.sort(key=lambda g: -g["duplicated_tokens"])

    return {
        "groups": groups,
        "totals": {
            "projects": len(groups),
            "runs": sum(g["runs"] for g in groups),
            "duplicated_tokens": sum(g["duplicated_tokens"] for g in groups),
            "headless_runs_seen": len(runs),
        },
        "thresholds": {
            "min_runs": min_runs,
            "ramp_peak_share": RAMP_PEAK_SHARE,
            "min_ramp_tokens": min_ramp_tokens,
        },
    }


def cold_start_report_data(transcript_root=None, project=None, *, window=None) -> dict:
    """Read transcripts and summarise, mirroring `report.band_report_data`."""
    from itertools import chain
    from .sink import read_events
    from .transcripts import read_transcripts
    from .window import scoped
    events = chain(read_transcripts(transcript_root), read_events())
    if project:
        events = (e for e in events if getattr(e, "project", None) == project)
    events = scoped(events, window)
    data = cold_start_summary(events)
    data["project"] = project
    data["window"] = window.label if window is not None else None
    return data
