from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

# An explicit time window, shared by every report.
#
# WHY THIS EXISTS. Every detector in tokendog answered "what is expensive across
# my history". None of them could answer "what happened between 17:29 and
# 17:45" — and that is the question a reader actually has when a limit has just
# gone. The named ranges the dashboard offers (today, 7d, 30d) do not help: a
# rate limit is consumed in minutes, and a calendar day is three orders of
# magnitude too coarse to see it.
#
# WHAT A WINDOW MEANS. It filters TURNS, not sessions. A session that opened
# yesterday and took four turns inside the window appears with those four
# turns — because the question is what was SPENT in the window, and a session's
# age is a separate fact about it. Lifetime figures that describe the session
# rather than the spend (how long it has been open, how long it has been idle)
# are deliberately still measured over its whole life; see `hygiene_summary`.
#
# COMPARE INSTANTS, NEVER STRINGS. Timestamps arrive as ISO text in mixed
# offsets, and `"2026-09-08T17:29:00-07:00" < "2026-09-08T18:00:00Z"` is true as
# text and false as time. Everything here parses to an aware instant and
# compares that. (This module exists partly because that bug was written once
# already, in a range filter that compared the strings.)
#
# LOCAL TIME IS WHAT THE READER TYPED. `--since 17:29` means 17:29 where the
# reader is sitting, so bare clock times and dates resolve against the local
# zone via `astimezone()`, which applies the offset in force ON THAT DATE — a
# fixed offset would be wrong for any window spanning a DST change.

RELATIVE = re.compile(r"^(\d+(?:\.\d+)?)\s*([mhdw])$", re.IGNORECASE)
CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
DATE_ONLY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

UNIT_MINUTES = {"m": 1.0, "h": 60.0, "d": 1440.0, "w": 10080.0}


class WindowError(ValueError):
    """A `--since`/`--until` value that cannot be read as a time."""


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _midnight(day, *, offset_days: int = 0) -> datetime:
    """Local midnight on `day` (+offset), as an aware instant."""
    naive = datetime.combine(day + timedelta(days=offset_days), time.min)
    return naive.astimezone(_local_tz()).astimezone(timezone.utc)


def parse_when(text, *, now=None, end: bool = False) -> datetime:
    """Read one `--since`/`--until` value as an instant.

    Accepted, in the order tried:
        90m 3h 2d 1w    that long before now
        now             this instant
        today           local midnight today (as an `until`: next midnight)
        yesterday       local midnight yesterday (as an `until`: today's)
        17:29           that clock time today, local; rolled back a day if it
                        has not happened yet, so `--since 23:50` at 00:10 means
                        ten minutes ago rather than a window in the future
        2026-09-08      local midnight that day (as an `until`: the day's end)
        2026-09-08 17:29 / 2026-09-08T17:29[:ss][±hh:mm]   as written; a value
                        with no offset is read as local time

    `end=True` marks the value as the CLOSING edge, which is the only thing that
    distinguishes `--until today` (meaning "up to the end of today") from
    `--since today` (meaning "from this morning").
    """
    if isinstance(text, datetime):
        return text if text.tzinfo else text.astimezone(_local_tz())
    raw = str(text or "").strip()
    if not raw:
        raise WindowError("empty time value")
    now = now or datetime.now(timezone.utc)
    lowered = raw.lower()

    if lowered == "now":
        return now

    rel = RELATIVE.match(lowered)
    if rel:
        minutes = float(rel.group(1)) * UNIT_MINUTES[rel.group(2).lower()]
        return now - timedelta(minutes=minutes)

    today = now.astimezone(_local_tz()).date()
    if lowered == "today":
        return _midnight(today, offset_days=1 if end else 0)
    if lowered == "yesterday":
        return _midnight(today, offset_days=0 if end else -1)

    clock = CLOCK.match(raw)
    if clock:
        hh, mm = int(clock.group(1)), int(clock.group(2))
        ss = int(clock.group(3) or 0)
        if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
            raise WindowError(f"'{raw}' is not a valid clock time")
        naive = datetime.combine(today, time(hh, mm, ss))
        when = naive.astimezone(_local_tz()).astimezone(timezone.utc)
        # A clock time still ahead of us today was meant as yesterday's.
        return when - timedelta(days=1) if when > now else when

    date = DATE_ONLY.match(raw)
    if date:
        try:
            day = datetime(int(date.group(1)), int(date.group(2)),
                           int(date.group(3))).date()
        except ValueError as exc:
            raise WindowError(f"'{raw}' is not a real date") from exc
        return _midnight(day, offset_days=1 if end else 0)

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WindowError(
            f"cannot read '{raw}' as a time — try 17:29, 2026-09-08, "
            f"2026-09-08T17:29, or a span like 90m / 3h / 2d") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone(_local_tz())
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Window:
    """A half-open interval `[since, until)`. Either edge may be open."""

    since: datetime | None = None
    until: datetime | None = None

    def contains(self, when) -> bool:
        """True if `when` falls inside. An unparseable instant is excluded.

        Excluding rather than including is the safe direction: a turn whose
        timestamp cannot be read has no place in a window the reader named, and
        counting it would attribute spend to a minute it may not belong to.
        """
        if when is None:
            return False
        if self.since is not None and when < self.since:
            return False
        if self.until is not None and when >= self.until:
            return False
        return True

    @property
    def minutes(self) -> float | None:
        if self.since is None or self.until is None:
            return None
        return (self.until - self.since).total_seconds() / 60.0

    @property
    def label(self) -> str:
        """How the window is named in a report header, in local time."""
        def fmt(dt, *, with_date: bool) -> str:
            local = dt.astimezone()
            return local.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M")

        if self.since is None and self.until is None:
            return "all time"
        if self.until is None:
            return f"since {fmt(self.since, with_date=True)}"
        if self.since is None:
            return f"up to {fmt(self.until, with_date=True)}"
        same_day = self.since.astimezone().date() == self.until.astimezone().date()
        span = self.minutes or 0
        length = (f"{span:.0f}m" if span < 90
                  else f"{span/60:.1f}h" if span < 48 * 60
                  else f"{span/1440:.1f}d")
        return (f"{fmt(self.since, with_date=True)} → "
                f"{fmt(self.until, with_date=not same_day)} ({length})")


def resolve(since=None, until=None, *, now=None) -> Window | None:
    """Build a `Window` from raw CLI values. `None` when neither was given.

    Returning `None` rather than an open window matters: a report can then tell
    "no window asked for" from "a window that happens to be unbounded", and
    only the first should print an all-time header.
    """
    if since is None and until is None:
        return None
    now = now or datetime.now(timezone.utc)
    lo = parse_when(since, now=now) if since is not None else None
    hi = parse_when(until, now=now, end=True) if until is not None else None
    if lo is not None and hi is not None and hi <= lo:
        raise WindowError(
            f"--until ({hi.astimezone():%Y-%m-%d %H:%M}) is not after "
            f"--since ({lo.astimezone():%Y-%m-%d %H:%M})")
    return Window(since=lo, until=hi)


def _ts(event) -> datetime | None:
    raw = getattr(event, "ts", None)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def scoped(events, window: Window | None):
    """Keep only the events inside `window`. Passes everything through if None."""
    if window is None:
        return events
    return (e for e in events if window.contains(_ts(e)))


def describe(window: Window | None) -> str:
    return window.label if window is not None else "all time"
