from datetime import datetime, timedelta, timezone

import pytest

from tokendog.event import RUNTIME_CLAUDE, SOURCE_TRANSCRIPT, TokenEvent
from tokendog.limit_resume import (
    GAP_MINUTES,
    MIN_CONTEXT,
    RESET_FLOOR,
    find_resumes,
    is_reset,
    resume_summary,
)

T0 = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def _t(minutes, ctx):
    """One turn: (timestamp at T0+minutes, occupancy)."""
    return (T0 + timedelta(minutes=minutes), ctx)


def _ev(minutes, ctx, transcript="t1", session="s1", project="demo", model="opus"):
    return TokenEvent(
        ts=(T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        session_id=session, transcript_id=transcript, runtime=RUNTIME_CLAUDE,
        event="assistant-turn", source=SOURCE_TRANSCRIPT,
        cache_read_tokens=ctx, output_tokens=100, model=model, project=project)


# --- what counts as a reset -------------------------------------------


def test_reset_needs_a_large_window_to_fall_from():
    """A small window shrinking is noise, not a reset worth crediting."""
    assert is_reset(RESET_FLOOR, RESET_FLOOR * 0.3) is True
    assert is_reset(RESET_FLOOR - 1, 10) is False


def test_a_shallow_drop_is_not_a_reset():
    assert is_reset(500_000, 400_000) is False
    assert is_reset(500_000, 200_000) is True


# --- detection --------------------------------------------------------


def test_needs_both_a_long_gap_and_a_large_window():
    long_gap_small_window = [_t(0, MIN_CONTEXT), _t(GAP_MINUTES + 10, 50_000)]
    assert find_resumes(long_gap_small_window) == []

    short_gap_large_window = [_t(0, MIN_CONTEXT), _t(1, MIN_CONTEXT + 10_000)]
    assert find_resumes(short_gap_large_window) == []


def test_a_qualifying_pause_is_a_resume():
    found = find_resumes([_t(0, MIN_CONTEXT), _t(GAP_MINUTES + 5, MIN_CONTEXT + 1)])
    assert len(found) == 1
    assert found[0]["context"] == MIN_CONTEXT + 1
    assert found[0]["gap_minutes"] == pytest.approx(GAP_MINUTES + 5)


def test_the_gap_boundary_is_inclusive():
    assert len(find_resumes([_t(0, MIN_CONTEXT), _t(GAP_MINUTES, MIN_CONTEXT)])) == 1


def test_resuming_into_a_reset_window_is_not_flagged():
    """Pausing and coming back to a CLEARED window is the behaviour we want."""
    turns = [_t(0, 900_000), _t(GAP_MINUTES + 10, 40_000)]
    assert find_resumes(turns) == []


def test_a_reset_that_still_lands_large_is_not_flagged():
    """900k -> 300k is a real reset even though 300k is over the floor."""
    turns = [_t(0, 900_000), _t(GAP_MINUTES + 10, 300_000)]
    assert find_resumes(turns) == []


def test_the_first_turn_can_never_be_a_resume():
    assert find_resumes([_t(0, 900_000)]) == []
    assert find_resumes([]) == []


# --- what carrying the window cost ------------------------------------


def test_carried_tokens_is_occupancy_times_the_turns_that_followed():
    turns = [_t(0, MIN_CONTEXT), _t(GAP_MINUTES + 1, 500_000),
             _t(GAP_MINUTES + 2, 510_000), _t(GAP_MINUTES + 3, 520_000)]
    r = find_resumes(turns)[0]
    assert r["turns_after"] == 2
    assert r["carried_tokens"] == 500_000 * 2


def test_a_resume_followed_immediately_by_a_reset_carried_nothing():
    turns = [_t(0, MIN_CONTEXT), _t(GAP_MINUTES + 1, 500_000), _t(GAP_MINUTES + 2, 20_000)]
    r = find_resumes(turns)[0]
    assert r["ended_with_reset"] is True
    assert r["turns_after"] == 0
    assert r["carried_tokens"] == 0


def test_a_window_carried_to_the_end_is_marked_never_reset():
    turns = [_t(0, MIN_CONTEXT), _t(GAP_MINUTES + 1, 500_000), _t(GAP_MINUTES + 2, 505_000)]
    r = find_resumes(turns)[0]
    assert r["ended_with_reset"] is False
    assert r["turns_after"] == 1


def test_turns_after_stops_at_the_reset_not_at_the_end():
    turns = [_t(0, MIN_CONTEXT), _t(GAP_MINUTES + 1, 500_000),
             _t(GAP_MINUTES + 2, 505_000), _t(GAP_MINUTES + 3, 30_000),
             _t(GAP_MINUTES + 4, 40_000), _t(GAP_MINUTES + 5, 50_000)]
    r = find_resumes(turns)[0]
    assert r["turns_after"] == 1
    assert r["ended_with_reset"] is True


# --- escalation -------------------------------------------------------


def test_escalation_is_reported_when_each_resume_starts_fuller():
    events = [
        _ev(0, MIN_CONTEXT), _ev(GAP_MINUTES + 1, 500_000),
        _ev(GAP_MINUTES + 2, 510_000),
        _ev(2 * GAP_MINUTES + 40, 700_000),
        _ev(3 * GAP_MINUTES + 90, 900_000),
    ]
    s = resume_summary(events)
    assert s["totals"]["count"] == 3
    row = s["sessions"][0]
    assert row["escalating"] is True
    assert (row["first_context"], row["last_context"]) == (500_000, 900_000)


def test_a_session_that_resets_between_resumes_is_not_escalating():
    events = [
        _ev(0, MIN_CONTEXT), _ev(GAP_MINUTES + 1, 900_000),
        _ev(GAP_MINUTES + 2, 30_000),
        _ev(2 * GAP_MINUTES + 40, 500_000),
    ]
    s = resume_summary(events)
    assert s["sessions"][0]["escalating"] is False


def test_a_single_resume_is_never_escalating():
    events = [_ev(0, MIN_CONTEXT), _ev(GAP_MINUTES + 1, 900_000)]
    assert resume_summary(events)["sessions"][0]["escalating"] is False


# --- grouping ---------------------------------------------------------


def test_transcripts_are_scored_separately_even_sharing_a_session_id():
    events = [
        _ev(0, MIN_CONTEXT, transcript="a", session="same"),
        _ev(GAP_MINUTES + 1, 500_000, transcript="a", session="same"),
        _ev(0, MIN_CONTEXT, transcript="b", session="same"),
        _ev(GAP_MINUTES + 1, 900_000, transcript="b", session="same"),
    ]
    s = resume_summary(events)
    assert s["totals"]["sessions"] == 2
    assert {r["peak_context"] for r in s["sessions"]} == {500_000, 900_000}


def test_interleaved_transcripts_are_ordered_before_detection():
    """Events arrive interleaved across files; each series must be sorted first."""
    events = [
        _ev(GAP_MINUTES + 1, 500_000, transcript="a"),
        _ev(0, MIN_CONTEXT, transcript="b"),
        _ev(0, MIN_CONTEXT, transcript="a"),
        _ev(GAP_MINUTES + 1, 600_000, transcript="b"),
    ]
    s = resume_summary(events)
    assert s["totals"]["count"] == 2


def test_non_turns_are_ignored():
    hook = TokenEvent(ts=T0.isoformat(), session_id="s1", runtime=RUNTIME_CLAUDE,
                      event="tool", tool_payload_tokens=999_999)
    assert hook.is_turn is False
    events = [hook, _ev(0, MIN_CONTEXT), _ev(GAP_MINUTES + 1, 500_000)]
    assert resume_summary(events)["totals"]["count"] == 1


def test_empty_input_summarises_to_zero_rather_than_raising():
    s = resume_summary([])
    assert s["totals"] == {"count": 0, "sessions": 0, "carried_tokens": 0,
                           "escalating_sessions": 0, "never_reset": 0}
    assert s["resumes"] == [] and s["sessions"] == []


def test_a_turn_with_an_unusable_timestamp_is_skipped():
    bad = _ev(0, MIN_CONTEXT)
    bad.ts = "not-a-date"
    events = [bad, _ev(GAP_MINUTES + 1, 500_000)]
    assert resume_summary(events)["totals"]["count"] == 0


# --- contract ---------------------------------------------------------


def test_thresholds_are_published_with_the_result():
    s = resume_summary([])
    assert s["thresholds"]["gap_minutes"] == GAP_MINUTES
    assert s["thresholds"]["min_context"] == MIN_CONTEXT


def test_min_context_is_derived_from_the_band_threshold():
    """So a session merely working at a normal size is never flagged."""
    from tokendog.bands import LARGE_CONTEXT_THRESHOLD
    assert MIN_CONTEXT == LARGE_CONTEXT_THRESHOLD * 2


def test_summary_is_json_serialisable():
    import json
    events = [_ev(0, MIN_CONTEXT), _ev(GAP_MINUTES + 1, 500_000)]
    s = resume_summary(events)
    assert json.loads(json.dumps(s))["totals"]["count"] == 1


def test_the_dashboard_and_the_detector_share_one_definition():
    """views re-exports rather than restating, so the two cannot disagree."""
    from tokendog import views
    assert views.RESUME_GAP_MIN == GAP_MINUTES
    assert views.RESUME_MIN_CONTEXT == MIN_CONTEXT


# --- attribution must not overlap -------------------------------------


def test_consecutive_resumes_do_not_both_claim_the_same_turns():
    """Each turn is charged to exactly one resume.

    Counting each resume's turns to the next RESET let consecutive resumes
    claim the same stretch, and the totals then exceeded the tokens the corpus
    actually contains. A resume's segment therefore stops at the next resume.
    """
    turns = [
        _t(0, MIN_CONTEXT),
        _t(GAP_MINUTES + 1, 500_000),        # resume A
        _t(GAP_MINUTES + 2, 505_000),        # 1 turn charged to A
        _t(2 * GAP_MINUTES + 40, 600_000),   # resume B
        _t(2 * GAP_MINUTES + 41, 605_000),   # 1 turn charged to B
    ]
    a, b = find_resumes(turns)
    assert a["turns_after"] == 1
    assert b["turns_after"] == 1


def test_total_carried_never_exceeds_the_tokens_carried():
    """The sum is bounded by what the turns after the first resume actually held."""
    turns = [_t(0, MIN_CONTEXT)] + [
        _t(GAP_MINUTES * (i + 1) + i, 500_000 + i * 1_000) for i in range(6)
    ]
    found = find_resumes(turns)
    ceiling = sum(c for _, c in turns)
    assert sum(r["carried_tokens"] for r in found) <= ceiling


def test_escalation_survives_a_dip_partway_through():
    """A long session always dips somewhere; the net rise is the signal."""
    events = [
        _ev(0, MIN_CONTEXT),
        _ev(GAP_MINUTES + 1, 500_000),
        _ev(2 * GAP_MINUTES + 40, 950_000),
        _ev(3 * GAP_MINUTES + 90, 600_000),   # the dip
        _ev(4 * GAP_MINUTES + 140, 870_000),
    ]
    row = resume_summary(events)["sessions"][0]
    assert row["escalating"] is True
    assert (row["first_context"], row["last_context"]) == (500_000, 870_000)
