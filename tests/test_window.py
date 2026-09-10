from datetime import datetime, timedelta, timezone

import pytest

from tokendog.window import (
    Window,
    WindowError,
    describe,
    parse_when,
    resolve,
    scoped,
)

# NOW is deliberately mid-afternoon UTC so that a bare clock time resolves
# either side of it depending on the machine's zone — the tests below assert on
# the RELATIONSHIP to now rather than on an absolute instant, so they hold
# wherever they are run.
NOW = datetime(2026, 9, 8, 21, 0, tzinfo=timezone.utc)


def _local_date(when=NOW):
    return when.astimezone().date()


# --- relative spans ---------------------------------------------------


@pytest.mark.parametrize("text,minutes", [
    ("90m", 90), ("3h", 180), ("2d", 2880), ("1w", 10080),
    ("30M", 30), ("1.5h", 90),
])
def test_a_span_is_measured_back_from_now(text, minutes):
    assert parse_when(text, now=NOW) == NOW - timedelta(minutes=minutes)


def test_now_is_now():
    assert parse_when("now", now=NOW) == NOW


# --- clock times ------------------------------------------------------


def test_a_clock_time_lands_on_that_time_locally():
    when = parse_when("09:30", now=NOW)
    local = when.astimezone()
    assert (local.hour, local.minute) == (9, 30)


def test_a_clock_time_still_ahead_today_is_read_as_yesterdays():
    """`--since 23:50` typed at 00:10 means ten minutes ago, not tomorrow.

    Without this a reader chasing a limit across midnight gets an empty report
    and no reason for it.
    """
    local_now = NOW.astimezone()
    ahead = (local_now + timedelta(hours=2)).strftime("%H:%M")
    when = parse_when(ahead, now=NOW)
    assert when < NOW
    assert NOW - when < timedelta(days=1)


def test_a_clock_time_accepts_seconds():
    local = parse_when("09:30:15", now=NOW).astimezone()
    assert (local.hour, local.minute, local.second) == (9, 30, 15)


@pytest.mark.parametrize("bad", ["25:00", "12:99"])
def test_an_impossible_clock_time_is_refused(bad):
    with pytest.raises(WindowError):
        parse_when(bad, now=NOW)


# --- dates and named days ---------------------------------------------


def test_a_date_is_local_midnight():
    when = parse_when("2026-09-08", now=NOW).astimezone()
    assert (when.year, when.month, when.day) == (2026, 9, 8)
    assert (when.hour, when.minute) == (0, 0)


def test_a_date_as_an_until_closes_at_the_end_of_that_day():
    """`--until 2026-09-08` must include the whole of the 8th, not none of it."""
    start = parse_when("2026-09-08", now=NOW)
    end = parse_when("2026-09-08", now=NOW, end=True)
    assert end - start == timedelta(days=1)


def test_today_as_since_is_this_morning_and_as_until_is_tomorrow():
    since = parse_when("today", now=NOW)
    until = parse_when("today", now=NOW, end=True)
    assert since <= NOW < until
    assert until - since == timedelta(days=1)


def test_yesterday_is_the_day_before_today():
    assert (parse_when("today", now=NOW) - parse_when("yesterday", now=NOW)
            == timedelta(days=1))


def test_yesterday_as_an_until_closes_where_today_opens():
    assert (parse_when("yesterday", now=NOW, end=True)
            == parse_when("today", now=NOW))


# --- ISO ---------------------------------------------------------------


def test_an_iso_value_with_an_offset_is_taken_at_its_word():
    assert (parse_when("2026-09-08T17:29:00-07:00", now=NOW)
            == datetime(2026, 9, 9, 0, 29, tzinfo=timezone.utc))


def test_a_z_suffix_is_utc():
    assert (parse_when("2026-09-08T17:29:00Z", now=NOW)
            == datetime(2026, 9, 8, 17, 29, tzinfo=timezone.utc))


def test_an_iso_value_without_an_offset_is_local():
    """It is what the reader typed, and the reader is not in UTC."""
    local = parse_when("2026-09-08T17:29", now=NOW).astimezone()
    assert (local.hour, local.minute) == (17, 29)


def test_a_datetime_passes_through_unchanged():
    when = datetime(2026, 9, 8, 17, 29, tzinfo=timezone.utc)
    assert parse_when(when) == when


@pytest.mark.parametrize("bad", ["lunchtime", "", "  ", "17-29", "next tuesday"])
def test_a_value_that_is_not_a_time_is_refused_with_a_usable_message(bad):
    with pytest.raises(WindowError) as exc:
        parse_when(bad, now=NOW)
    # The message has to name the forms that work, or the reader guesses again.
    if bad.strip():
        assert "17:29" in str(exc.value)


# --- resolve ----------------------------------------------------------


def test_no_flags_means_no_window():
    """`None` and "an unbounded window" are different: only the first is
    all-time, and only the second should print a window header."""
    assert resolve() is None


def test_one_sided_windows_are_allowed():
    assert resolve(since="3h").until is None
    assert resolve(until="3h").since is None


def test_an_until_at_or_before_the_since_is_refused():
    with pytest.raises(WindowError) as exc:
        resolve(since="17:00", until="16:00", now=NOW)
    assert "not after" in str(exc.value)
    with pytest.raises(WindowError):
        resolve(since="17:00", until="17:00", now=NOW)


# --- containment ------------------------------------------------------


def test_the_interval_is_half_open():
    """Since is inclusive, until exclusive — so back-to-back windows tile
    without double-counting the turn on the boundary."""
    lo = datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc)
    hi = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    w = Window(since=lo, until=hi)
    assert w.contains(lo)
    assert w.contains(hi - timedelta(seconds=1))
    assert not w.contains(hi)
    assert not w.contains(lo - timedelta(seconds=1))


def test_an_unreadable_instant_is_excluded_not_included():
    """A turn whose timestamp cannot be read has no place in a named window;
    counting it would attribute spend to a minute it may not belong to."""
    assert not Window(since=NOW).contains(None)


def test_an_open_window_contains_everything():
    assert Window().contains(NOW)


def test_containment_compares_instants_not_strings():
    """The bug this module exists to prevent.

    `"2026-09-08T17:29:00-07:00" < "2026-09-08T18:00:00Z"` is TRUE as text and
    FALSE as time — the first is 00:29 the next day in UTC. A range filter that
    compared the ISO strings silently included turns from outside the window.
    """
    raw_since = "2026-09-08T18:00:00Z"
    raw_later = "2026-09-08T17:29:00-07:00"
    assert raw_later < raw_since            # as raw text, it looks earlier

    since = parse_when(raw_since)
    later = parse_when(raw_later)
    assert later > since                    # parsed, it is not
    assert Window(since=since).contains(later)


def test_a_window_spanning_a_dst_change_uses_each_days_own_offset():
    """A fixed offset would be wrong for one end of the window."""
    a = parse_when("2026-01-15T12:00", now=NOW)   # standard time locally
    b = parse_when("2026-07-15T12:00", now=NOW)   # summer time locally
    assert a.astimezone().hour == 12
    assert b.astimezone().hour == 12


# --- minutes and label ------------------------------------------------


def test_minutes_is_none_for_an_open_window():
    assert Window(since=NOW).minutes is None


def test_minutes_measures_the_interval():
    w = Window(since=NOW, until=NOW + timedelta(minutes=16))
    assert w.minutes == pytest.approx(16.0)


def test_a_short_same_day_window_reads_as_a_range_with_its_length():
    w = resolve(since="17:29", until="17:45", now=NOW)
    label = w.label
    assert "17:29" in label and "17:45" in label and "16m" in label


def test_a_one_sided_window_says_since():
    assert resolve(since="17:29", now=NOW).label.startswith("since ")


def test_no_window_describes_itself_as_all_time():
    assert describe(None) == "all time"


# --- scoped -----------------------------------------------------------


class _E:
    def __init__(self, ts):
        self.ts = ts


def test_scoped_passes_everything_through_when_there_is_no_window():
    events = [_E("2020-01-01T00:00:00Z")]
    assert list(scoped(events, None)) == events


def test_scoped_keeps_only_what_is_inside():
    inside = _E("2026-09-08T17:30:00Z")
    before = _E("2026-09-08T17:00:00Z")
    after = _E("2026-09-08T18:00:00Z")
    w = Window(since=datetime(2026, 9, 8, 17, 29, tzinfo=timezone.utc),
               until=datetime(2026, 9, 8, 17, 45, tzinfo=timezone.utc))
    assert list(scoped([before, inside, after], w)) == [inside]


def test_scoped_drops_an_event_with_no_readable_timestamp():
    w = Window(since=datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc))
    assert list(scoped([_E(None), _E("not a time")], w)) == []


def test_scoped_reads_a_naive_timestamp_as_utc():
    """Transcript timestamps are UTC; a naive one is not a reason to drop it."""
    w = Window(since=datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc))
    assert len(list(scoped([_E("2026-09-08T17:30:00")], w))) == 1
