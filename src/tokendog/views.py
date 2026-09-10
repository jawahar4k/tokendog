from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import chain
from typing import Iterable, Iterator

from .hygiene import (
    AGE_LONG_LIVED_H,
    IDLE_STALE_H,
    OCCUPANCY_ALARM,
    OCCUPANCY_WARN,
    STALE_MIN_CONTEXT,
    classify,
    is_subagent,
    segment,
    session_findings,
)
from .limit_resume import GAP_MINUTES, MIN_CONTEXT, find_resumes, is_reset
from .sink import read_events
from .transcripts import read_transcripts

# The view-model behind `tokendog serve`. Everything here is JSON-serialisable
# and stable enough to be the export contract: one place defines what a band
# is, what a resume-at-the-wall is, and what makes a session worth acting on,
# so a second surface renders those definitions instead of re-deriving them.
#
# Roll-ups key on TRANSCRIPT, not session id — one transcript file is one
# context that grew on its own, whereas a resumed session and its subagent
# transcripts all share the parent's session id. `bands.band_summary` keys its
# peaks the same way and for the same reason; keeping the two consistent is the
# point.

WINDOW_DAYS = 7          # the costing window every headline figure covers
TREND_DAYS = 14          # the daily series, wider so the window has context

# Every occupancy / age / idle threshold, and the severity rules that use them,
# are defined in `hygiene` and imported here. The dashboard and `tokendog
# hygiene` therefore cannot disagree about what makes a session worth acting on.

# Resume-at-the-wall is defined once, in `limit_resume`, and re-exported here
# so the dashboard and `tokendog resumes` cannot disagree about what one is.
RESUME_GAP_MIN = GAP_MINUTES
RESUME_MIN_CONTEXT = MIN_CONTEXT

# Bands for presentation. Narrower than `bands.BANDS` at the bottom (the two
# smallest are folded together) and wider at the top, because the actionable
# detail is all above 400k — and because a five-step ordinal ramp is the most
# a single-hue scale renders with visible separation between steps.
VIEW_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("<100K", 0, 100_000),
    ("100-200K", 100_000, 200_000),
    ("200-400K", 200_000, 400_000),
    ("400-700K", 400_000, 700_000),
    ("700K+", 700_000, None),
)


def _parse(ts) -> datetime | None:
    """Parse a transcript timestamp, or None if it is unusable.

    Returning None rather than raising matters: one malformed timestamp in a
    413MB tree should cost that turn, not the whole report.
    """
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def view_band_for(context: int) -> str:
    n = max(0, int(context or 0))
    for label, lower, upper in VIEW_BANDS:
        if n >= lower and (upper is None or n < upper):
            return label
    return VIEW_BANDS[-1][0]


def _turns(events: Iterable, project: str | None) -> Iterator:
    for e in events:
        if not getattr(e, "is_turn", False):
            continue
        if project and getattr(e, "project", None) != project:
            continue
        yield e


# Where a command is typed. A reader who cannot tell a shell command from an
# in-session one will try the wrong surface and conclude the tool is broken.
TERMINAL = "terminal"      # a shell, outside any session
SESSION = "session"        # typed into the coding agent mid-session
CONFIG = "config"          # an edit to a file, applied by a shell command

# How soon, judged on BOTH size and cost-to-act. A big saving that costs the
# reader their working state is not more urgent than a small one that costs a
# keystroke — ranking on tokens alone would put the painful items first.
URGENT, SOON, CONSIDER = "now", "soon", "consider"

_EFFORT_URGENCY = {
    "one keystroke": URGENT,
    "config edit": URGENT,
    "habit": SOON,
    "pipeline change": SOON,
    "loses working state": CONSIDER,
}


def _label(session_id: str, project: str | None) -> str:
    """A session id is unmemorable; the project it ran in is what a reader knows."""
    return f"{session_id} · {project}" if project else session_id


def _recommendations(findings: list, surface: dict | None,
                     sessions: list) -> list[dict]:
    """One ranked list of things to actually do, drawn from every detector.

    Sorted by urgency first and size second, so the cheap-and-large items lead
    and the ones that cost the reader something sit below them — not by which
    detector produced them.
    """
    out: list[dict] = []
    by_id = {s["id"]: s for s in sessions}

    for c in ((surface or {}).get("connectors") or []):
        if c["severity"] == "unused" and c["enabled"]:
            out.append({
                "kind": "disable-connector", "target": c["connector"],
                "label": c["connector"], "project": None,
                "title": f"Disable {c['connector']}",
                "why": (f"resident on {c['turns_resident']:,} turns, never called once"
                        + (f", carrying {c['carried_tokens']:,} tokens"
                           if c["carried_tokens"] else "")),
                # None, not 0: an unreachable connector could not be sized, and
                # showing it as zero would rank the cheapest win last.
                "tokens": c["carried_tokens"],
                "command": f"tokendog surface --disable {c['connector']} --apply",
                "command_kind": CONFIG, "effort": "config edit",
            })

    for f in sorted([x for x in findings if x["kind"] == "stale-open"],
                    key=lambda x: -x["last_context"])[:5]:
        row = by_id.get(f["session"], {})
        project = f.get("project") or row.get("project")
        out.append({
            "kind": "close-session", "target": f["session"],
            "label": _label(f["session"], project), "project": project,
            "title": f"Close session {_label(f['session'], project)}",
            "why": f"idle {f['idle_hours']:.0f}h still holding {f['last_context']:,} tokens",
            "tokens": f["last_context"], "command": "/clear",
            "command_kind": SESSION, "effort": "one keystroke",
        })

    for f in sorted([x for x in findings if x["kind"] == "cold-start-dup"],
                    key=lambda x: -x.get("input", 0))[:3]:
        out.append({
            "kind": "preload-context", "target": f["project"],
            "label": f["project"], "project": f["project"],
            "title": f"Pre-load shared context for {f['project']} runs",
            "why": f"{f['runs']} headless runs each rediscovered the same material",
            "tokens": f.get("input", 0),
            "command": f"tokendog coldstart --project {f['project']}",
            "command_kind": TERMINAL, "effort": "pipeline change",
        })

    resumes = [f for f in findings if f["kind"] == "limit-resume"]
    if resumes:
        worst = max(resumes, key=lambda f: f.get("context", 0))
        project = worst.get("project")
        out.append({
            "kind": "reset-on-limit", "target": "habit",
            "label": _label(worst["session"], project), "project": project,
            "title": "Reset when you hit a limit, don't resume",
            "why": (f"{len(resumes)} resume(s) carried a large window past the moment to clear it"
                    f" — worst was {worst.get('context', 0):,} tokens"),
            "tokens": sum(f.get("carried_tokens", 0) or 0 for f in resumes),
            "command": "/clear", "command_kind": SESSION, "effort": "habit",
        })

    for row in [x for x in sessions if x["severity"] == "ser"][:3]:
        label = _label(row["id"], row.get("project"))
        out.append({
            "kind": "retire-session", "target": row["id"],
            "label": label, "project": row.get("project"),
            "title": f"Retire session {label}",
            "why": (f"open {row['span_hours']:.0f}h at {row['avg_context']:,} average context"
                    f", {row['resets']} reset(s)"),
            "tokens": row["input"], "command": "/clear",
            "command_kind": SESSION, "effort": "loses working state",
        })

    for r in out:
        r["urgency"] = _EFFORT_URGENCY.get(r["effort"], SOON)
    rank = {URGENT: 0, SOON: 1, CONSIDER: 2}
    out.sort(key=lambda r: (rank.get(r["urgency"], 9), -(r["tokens"] or 0)))
    return out


def ledger_data(transcript_root=None, project=None, *, now=None,
                window_days: int = WINDOW_DAYS,
                trend_days: int = TREND_DAYS,
                session_limit: int = 20,
                include_surface: bool = True,
                since=None, until=None, window_label: str | None = None) -> dict:
    """Assemble every figure the dashboard shows, in one pass over the events.

    `now` is injectable so the windows are testable without freezing the clock.

    The PREVIOUS window of the same length is measured too, so every headline
    can be read as a change rather than an absolute — a number with nothing to
    compare it to cannot tell you whether things are getting better.
    """
    now = now or datetime.now(timezone.utc)
    # A window can be a rolling span (last 7 days) or a CALENDAR one (today,
    # yesterday), and the two are different questions. A caller that knows which
    # passes the bounds; otherwise they come from window_days.
    window_end = until or now
    window_start = since or (window_end - timedelta(days=window_days))
    span = window_end - window_start
    # The comparison window is the same LENGTH immediately before this one, so
    # "today" compares against yesterday and "last 7 days" against the 7 before.
    prev_start = window_start - span
    trend_start = min(prev_start, now - timedelta(days=trend_days))

    events = chain(read_transcripts(transcript_root), read_events())

    per: dict[str, dict] = defaultdict(
        lambda: {"seq": [], "project": None, "headless": False,
                 "models": defaultdict(int)})
    daily: dict[str, dict] = defaultdict(lambda: {"input": 0, "output": 0, "turns": 0})
    band_turns: dict[str, int] = {label: 0 for label, _, _ in VIEW_BANDS}
    band_tokens: dict[str, int] = {label: 0 for label, _, _ in VIEW_BANDS}
    total_in = total_out = total_turns = 0
    prev_in = prev_out = prev_turns = 0
    prev_heavy_tokens = prev_heavy_turns = 0

    for e in _turns(events, project):
        when = _parse(e.ts)
        if when is None:
            continue
        ctx = int(e.context_tokens or 0)
        out = int(e.output_tokens or 0)
        key = getattr(e, "transcript_id", None) or e.session_id or "unknown"
        rec = per[key]
        rec["seq"].append((when, ctx, out))
        rec["project"] = rec["project"] or getattr(e, "project", None)
        rec["headless"] = rec["headless"] or bool(getattr(e, "is_headless", False))
        if getattr(e, "model", None):
            rec["models"][e.model] += 1

        if when >= trend_start:
            day = daily[when.astimezone().strftime("%Y-%m-%d")]
            day["input"] += ctx
            day["output"] += out
            day["turns"] += 1

        if window_start <= when <= window_end:
            label = view_band_for(ctx)
            band_turns[label] += 1
            band_tokens[label] += ctx
            total_in += ctx
            total_out += out
            total_turns += 1
        elif prev_start <= when < window_start:
            prev_in += ctx
            prev_out += out
            prev_turns += 1
            if ctx >= OCCUPANCY_ALARM:
                prev_heavy_tokens += ctx
                prev_heavy_turns += 1

    def pct(part: int, whole: int) -> float:
        return round(part / whole * 100.0, 1) if whole else 0.0

    # --- sessions -------------------------------------------------------
    sessions: list[dict] = []
    findings: list[dict] = []
    for key, rec in per.items():
        seq = sorted(rec["seq"], key=lambda r: r[0])
        window = [r for r in seq if window_start <= r[0] <= window_end]
        if not window:
            continue
        ctxs = [c for _, c, _ in window]
        s_in = sum(ctxs)
        s_out = sum(o for _, _, o in window)
        # Burn: input tokens per minute across the in-range turns. The figure
        # that tells a runaway (a bucket emptied in minutes) from a grinder (the
        # same total spread over hours) — the drilldown shows it, the table
        # should too. None for a single turn, which has no elapsed span.
        span_min = (window[-1][0] - window[0][0]).total_seconds() / 60.0
        burn = round(s_in / span_min, 1) if span_min > 0 else None
        span_h = round((seq[-1][0] - seq[0][0]).total_seconds() / 3600.0, 1)
        idle_h = round((now - seq[-1][0]).total_seconds() / 3600.0, 1)
        headless = rec["headless"]
        avg_ctx = s_in // len(window)
        last_ctx = seq[-1][1]
        resets = sum(1 for (_, a, _), (_, b, _) in zip(seq, seq[1:]) if is_reset(a, b))
        subagent = is_subagent(key)
        # One transcript can hold many contexts — a reset does not close the
        # file. Lifetime figures say what the session COST; the live segment
        # says what it IS, and that is what the verdict should read.
        segs = segment([(t, c) for t, c, _ in seq])
        live = segs[-1] if segs else None
        severity, action = classify(
            avg_context=(live or {}).get("avg_context", avg_ctx),
            last_context=last_ctx, span_hours=span_h,
            idle_hours=idle_h, resets=resets, no_live_window=headless or subagent,
            kind="subagent" if subagent else ("headless" if headless else "interactive"))
        sessions.append({
            "id": key[:8],
            "transcript": key,
            "project": rec["project"] or "unknown",
            "headless": headless,
            "turns": len(window),
            "input": s_in,
            "output": s_out,
            "ratio": round(s_in / s_out) if s_out else None,
            "avg_context": avg_ctx,
            "peak_context": max(ctxs),
            "last_context": last_ctx,
            "burn_per_min": burn,
            # Minutes the in-window turns span. Burn = input / this, so a tiny
            # value makes burn a provisional rate the UI should mark rather than
            # present as a steady per-minute figure.
            "window_minutes": round(span_min, 2),
            # Last activity, as an epoch second — the default sort key, so the
            # table opens on what you just touched rather than on what cost most.
            # In-window last, since the row describes the session inside the range.
            "last_ts": window[-1][0].timestamp(),
            "last_at": window[-1][0].astimezone().isoformat(timespec="minutes"),
            "span_hours": span_h,
            "idle_hours": idle_h,
            "model": max(rec["models"], key=rec["models"].get) if rec["models"] else None,
            "resets": resets,
            "segments": len(segs),
            "current": live,
            "severity": severity,
            "action": action,
        })

        # One definition of these, in `hygiene`. The copy that used to live here
        # had no no-live-window guard on `long-lived` at all, so the dashboard
        # reported headless runs and subagent transcripts as long-lived while
        # `tokendog hygiene` correctly did not.
        findings.extend(session_findings(
            key, rec["project"], contexts=[c for _, c, _ in seq],
            no_live_window=headless or subagent, span_hours=span_h,
            idle_hours=idle_h, last_context=last_ctx, avg_context=avg_ctx,
            resets=resets))

        # Resume-at-the-wall, in order, so the escalation is visible. The
        # detection itself belongs to `limit_resume`; this only scopes it to
        # the window and reshapes it as a finding.
        for r in find_resumes([(t, c) for t, c, _ in seq]):
            # Compare parsed instants, not ISO strings: across a DST change the
            # two offsets differ and a lexicographic compare silently misorders.
            when_r = _parse(r["at"])
            if when_r is not None and when_r >= window_start:
                findings.append({"kind": "limit-resume", "session": key[:8],
                                 "project": rec["project"], "at": r["at"],
                                 "context": r["context"],
                                 "gap_minutes": round(r["gap_minutes"]),
                                 "turns_after": r["turns_after"],
                                 "carried_tokens": r["carried_tokens"],
                                 "ended_with_reset": r["ended_with_reset"]})

    # Sorted by input so `sessions[0]` is the heaviest (the verdict narrative and
    # the `[:session_limit]` truncation both rely on that — keep the biggest, not
    # the most recent). The dashboard re-sorts the TABLE by last-activity for
    # display and makes every column click-sortable; `last_ts` carries the key.
    sessions.sort(key=lambda s: -s["input"])

    # Cold-start duplication: several headless runs against one project each
    # paying full discovery cost for what the previous run already read.
    by_project: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        if s["headless"]:
            by_project[s["project"]].append(s)
    for proj, runs in by_project.items():
        if len(runs) > 1:
            findings.append({"kind": "cold-start-dup", "project": proj,
                             "runs": len(runs),
                             "input": sum(r["input"] for r in runs),
                             "sessions": [r["id"] for r in runs]})

    # --- concentration headline ----------------------------------------
    heavy_turns = sum(n for label, n in band_turns.items()
                      if label in ("400-700K", "700K+"))
    heavy_tokens = sum(n for label, n in band_tokens.items()
                       if label in ("400-700K", "700K+"))
    warn_turns = heavy_turns + band_turns["200-400K"]
    warn_tokens = heavy_tokens + band_tokens["200-400K"]

    counts = defaultdict(int)
    for s in sessions:
        counts[s["severity"]] += 1

    def change(current: float, previous: float) -> float | None:
        """Percent change, or None when there is no baseline to change from."""
        if not previous:
            return None
        return round((current - previous) / previous * 100.0, 1)

    surface = None
    if include_surface:
        # Its own read: this function has already consumed the event stream, and
        # the surface question is answered from config plus tool-call counts
        # rather than from occupancy, so it does not share this pass.
        from .surface import surface_report_data
        try:
            surface = surface_report_data(transcript_root, project)
        except Exception:
            surface = None

    return {
        "generated_at": now.astimezone().isoformat(timespec="seconds"),
        "window_days": window_days,
        "window_label": window_label or f"{window_days}d",
        "window_start": window_start.astimezone().isoformat(timespec="minutes"),
        "window_end": window_end.astimezone().isoformat(timespec="minutes"),
        "project": project,
        "previous": {
            "input": prev_in, "output": prev_out, "turns": prev_turns,
            "ratio": round(prev_in / prev_out) if prev_out else None,
            "heavy_tokens": prev_heavy_tokens, "heavy_turns": prev_heavy_turns,
        },
        "change": {
            "input": change(total_in, prev_in),
            "output": change(total_out, prev_out),
            "turns": change(total_turns, prev_turns),
            "heavy_tokens": change(heavy_tokens, prev_heavy_tokens),
        },
        "surface": surface,
        "recommendations": _recommendations(findings, surface, sessions),
        "headline": {
            "input": total_in,
            "output": total_out,
            "turns": total_turns,
            "ratio": round(total_in / total_out) if total_out else None,
            "heavy": {"threshold": OCCUPANCY_ALARM, "turns": heavy_turns,
                      "tokens": heavy_tokens,
                      "pct_turns": pct(heavy_turns, total_turns),
                      "pct_tokens": pct(heavy_tokens, total_in)},
            "warn": {"threshold": OCCUPANCY_WARN, "turns": warn_turns,
                     "tokens": warn_tokens,
                     "pct_turns": pct(warn_turns, total_turns),
                     "pct_tokens": pct(warn_tokens, total_in)},
        },
        "bands": [{"band": label, "turns": band_turns[label],
                   "tokens": band_tokens[label],
                   "pct_tokens": pct(band_tokens[label], total_in)}
                  for label, _, _ in VIEW_BANDS],
        "daily": [{"date": d, **daily[d]} for d in sorted(daily)],
        "sessions": sessions[:session_limit],
        "session_count": len(sessions),
        "severity_counts": dict(counts),
        "findings": findings,
        "thresholds": {
            "occupancy_warn": OCCUPANCY_WARN,
            "occupancy_alarm": OCCUPANCY_ALARM,
            "age_long_lived_hours": AGE_LONG_LIVED_H,
            "idle_stale_hours": IDLE_STALE_H,
            "stale_min_context": STALE_MIN_CONTEXT,
            "resume_gap_minutes": RESUME_GAP_MIN,
            "resume_min_context": RESUME_MIN_CONTEXT,
        },
    }
