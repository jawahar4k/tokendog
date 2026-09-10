from datetime import datetime, timedelta, timezone

import pytest

from tokendog.event import RUNTIME_CLAUDE, SOURCE_TRANSCRIPT, TokenEvent
from tokendog.hygiene import (
    AGE_LONG_LIVED_H,
    IDLE_STALE_H,
    OCCUPANCY_ALARM,
    OCCUPANCY_WARN,
    STALE_MIN_CONTEXT,
    classify,
    count_resets,
    excess,
    hygiene_summary,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _turn(hours_ago, ctx, *, transcript="s1", project="demo", entrypoint="cli"):
    return TokenEvent(
        ts=(NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        session_id=transcript, transcript_id=transcript, runtime=RUNTIME_CLAUDE,
        event="assistant-turn", source=SOURCE_TRANSCRIPT,
        cache_read_tokens=ctx, output_tokens=50, project=project,
        entrypoint=entrypoint)


def _one(events, **kw):
    return hygiene_summary(events, now=NOW, **kw)["sessions"][0]


# --- excess -----------------------------------------------------------


def test_excess_counts_only_the_part_above_the_line():
    turns, tokens = excess([OCCUPANCY_WARN + 1_000, OCCUPANCY_WARN + 4_000])
    assert (turns, tokens) == (2, 5_000)


def test_a_session_under_the_threshold_has_no_excess():
    """The measure stays silent about sessions doing nothing wrong."""
    assert excess([10_000, 50_000, OCCUPANCY_WARN]) == (0, 0)


def test_the_threshold_itself_is_not_excess():
    assert excess([OCCUPANCY_WARN]) == (0, 0)
    assert excess([OCCUPANCY_WARN + 1]) == (1, 1)


def test_excess_needs_no_counterfactual_to_be_bounded():
    """It can never exceed the tokens actually carried."""
    contexts = [500_000, 900_000, 300_000]
    _, tokens = excess(contexts)
    assert tokens < sum(contexts)


def test_excess_threshold_is_adjustable():
    assert excess([300_000], threshold=250_000) == (1, 50_000)


# --- resets -----------------------------------------------------------


def test_resets_are_counted_from_occupancy_collapses():
    assert count_resets([500_000, 40_000, 200_000, 900_000, 50_000]) == 2


def test_a_steadily_growing_session_never_reset():
    assert count_resets([100_000, 300_000, 600_000, 900_000]) == 0


def test_reset_definition_is_shared_with_the_resume_detector():
    """One definition of a reset, so the two detectors cannot disagree."""
    from tokendog.limit_resume import is_reset
    assert is_reset(500_000, 100_000) is True
    assert count_resets([500_000, 100_000]) == 1


# --- classification ---------------------------------------------------


def test_a_loaded_idle_session_outranks_a_merely_old_one():
    """Cheapest to fix, most expensive to ignore — so it is reported first."""
    sev, action = classify(avg_context=300_000, last_context=STALE_MIN_CONTEXT,
                           span_hours=1_000, idle_hours=IDLE_STALE_H, resets=0)
    assert (sev, action) == ("crit", "Close it now")


def test_a_loaded_active_session_is_told_to_clear():
    sev, action = classify(avg_context=300_000, last_context=OCCUPANCY_ALARM,
                           span_hours=1, idle_hours=0, resets=1)
    assert (sev, action) == ("crit", "Clear and restart")


def test_an_old_session_is_told_to_retire():
    sev, action = classify(avg_context=50_000, last_context=50_000,
                           span_hours=AGE_LONG_LIVED_H, idle_hours=0, resets=2)
    assert (sev, action) == ("ser", "Retire the session")


def test_a_never_reset_session_is_named_as_such():
    """Different advice from a busy session that resets: this one was unmanaged."""
    _, never = classify(avg_context=OCCUPANCY_WARN, last_context=10_000,
                        span_hours=1, idle_hours=0, resets=0)
    _, managed = classify(avg_context=OCCUPANCY_WARN, last_context=10_000,
                          span_hours=1, idle_hours=0, resets=3)
    assert never == "Never reset — compact at 120K"
    assert managed == "Compact at 120K"


def test_a_small_recent_session_is_healthy():
    assert classify(avg_context=40_000, last_context=40_000, span_hours=0.5,
                    idle_hours=0.1, resets=0) == ("ok", "Healthy shape")


@pytest.mark.parametrize("avg,expected", [
    (OCCUPANCY_WARN, "Pre-load shared context"),
    (10_000, "Healthy shape"),
])
def test_headless_runs_are_judged_only_on_what_they_read(avg, expected):
    """An exited run holds no window, so age and idleness say nothing about it."""
    sev, action = classify(avg_context=avg, last_context=900_000, span_hours=5_000,
                           idle_hours=5_000, resets=0, no_live_window=True,
                           kind="headless")
    assert action == expected


# --- summary ----------------------------------------------------------


def test_span_and_idle_are_measured_separately():
    s = _one([_turn(10, 10_000), _turn(4, 20_000)])
    assert s["span_hours"] == pytest.approx(6.0, abs=0.1)
    assert s["idle_hours"] == pytest.approx(4.0, abs=0.1)


def test_drift_is_reported_as_first_to_peak():
    s = _one([_turn(5, 60_000), _turn(4, 500_000), _turn(3, 900_000)])
    assert s["first_context"] == 60_000
    assert s["peak_context"] == 900_000
    assert s["resets"] == 0


def test_an_unmanaged_session_is_flagged():
    d = hygiene_summary([_turn(5, 60_000), _turn(4, OCCUPANCY_ALARM)], now=NOW)
    drift = [f for f in d["findings"] if f["kind"] == "unmanaged-drift"]
    assert len(drift) == 1
    assert drift[0]["peak_context"] == OCCUPANCY_ALARM


def test_a_session_that_reset_is_not_unmanaged_drift():
    d = hygiene_summary([_turn(5, 900_000), _turn(4, 40_000)], now=NOW)
    assert not any(f["kind"] == "unmanaged-drift" for f in d["findings"])


def test_long_lived_and_stale_are_not_raised_for_headless_runs():
    events = [_turn(5_000, 900_000, entrypoint="sdk-cli"),
              _turn(4_000, 900_000, entrypoint="sdk-cli")]
    d = hygiene_summary(events, now=NOW)
    kinds = {f["kind"] for f in d["findings"]}
    assert "long-lived" not in kinds
    assert "stale-open" not in kinds


def test_span_covers_the_whole_life_even_when_the_window_is_scoped():
    """A session opened last week is old regardless of the report's window."""
    events = [_turn(200, 10_000), _turn(1, 20_000)]
    since = NOW - timedelta(hours=24)
    s = hygiene_summary(events, now=NOW, since=since)["sessions"][0]
    assert s["turns"] == 1                       # only the recent turn counted
    assert s["span_hours"] == pytest.approx(199.0, abs=0.5)


def test_sessions_are_ordered_by_excess():
    events = ([_turn(3, 250_000, transcript="small")]
              + [_turn(3, 900_000, transcript="big")])
    rows = hygiene_summary(events, now=NOW)["sessions"]
    assert rows[0]["transcript"] == "big"


def test_transcripts_are_assessed_separately_even_sharing_a_session_id():
    a = _turn(3, 900_000, transcript="t1")
    b = _turn(3, 100_000, transcript="t2")
    a.session_id = b.session_id = "same"
    assert hygiene_summary([a, b], now=NOW)["totals"]["sessions"] == 2


def test_empty_input_summarises_to_zero_rather_than_raising():
    d = hygiene_summary([], now=NOW)
    assert d["totals"]["sessions"] == 0
    assert d["sessions"] == [] and d["findings"] == []


def test_a_turn_with_an_unusable_timestamp_is_skipped():
    bad = _turn(3, 900_000)
    bad.ts = "not-a-date"
    assert hygiene_summary([bad], now=NOW)["totals"]["sessions"] == 0


# --- contract ---------------------------------------------------------


def test_thresholds_are_published_with_the_result():
    th = hygiene_summary([], now=NOW)["thresholds"]
    assert th["occupancy_warn"] == OCCUPANCY_WARN
    assert th["age_long_lived_hours"] == AGE_LONG_LIVED_H


def test_warn_threshold_is_not_restated():
    from tokendog.bands import LARGE_CONTEXT_THRESHOLD
    assert OCCUPANCY_WARN == LARGE_CONTEXT_THRESHOLD


def test_summary_is_json_serialisable():
    import json
    d = hygiene_summary([_turn(3, 900_000)], now=NOW)
    assert json.loads(json.dumps(d))["totals"]["sessions"] == 1


def test_the_dashboard_shares_one_set_of_thresholds_and_rules():
    """views imports them; it no longer keeps its own copy or its own classifier."""
    from tokendog import views
    assert views.OCCUPANCY_WARN == OCCUPANCY_WARN
    assert views.OCCUPANCY_ALARM == OCCUPANCY_ALARM
    assert views.AGE_LONG_LIVED_H == AGE_LONG_LIVED_H
    assert views.IDLE_STALE_H == IDLE_STALE_H
    assert views.STALE_MIN_CONTEXT == STALE_MIN_CONTEXT
    assert not hasattr(views, "_classify")


# --- subagent transcripts ---------------------------------------------


def test_a_subagent_transcript_is_recognised_by_its_name():
    from tokendog.hygiene import is_subagent
    assert is_subagent("agent-a35a89b715b8fad44") is True
    assert is_subagent("2c8e4dea-aaf0-4b5b-a714-d7c35b1df0d6") is False
    assert is_subagent(None) is False


def test_a_subagent_is_never_told_to_close_itself():
    """Its context ends when the subagent does; there is nothing to reset."""
    sev, action = classify(avg_context=900_000, last_context=900_000,
                           span_hours=5_000, idle_hours=5_000, resets=0,
                           no_live_window=True, kind="subagent")
    assert action == "Scope the subagent"


def test_a_small_subagent_is_healthy():
    assert classify(avg_context=20_000, last_context=20_000, span_hours=1,
                    idle_hours=900, resets=0, no_live_window=True,
                    kind="subagent") == ("ok", "Healthy shape")


def test_subagent_rows_carry_their_kind_and_skip_age_findings():
    events = [_turn(900, 900_000, transcript="agent-abc123"),
              _turn(800, 900_000, transcript="agent-abc123")]
    d = hygiene_summary(events, now=NOW)
    row = d["sessions"][0]
    assert (row["kind"], row["subagent"]) == ("subagent", True)
    assert row["action"] == "Scope the subagent"
    kinds = {f["kind"] for f in d["findings"]}
    assert "long-lived" not in kinds and "stale-open" not in kinds


def test_a_subagent_still_reports_its_excess():
    """What it carried was real, even though no one can close it."""
    row = _one([_turn(3, 900_000, transcript="agent-abc123")])
    assert row["excess_tokens"] == 900_000 - OCCUPANCY_WARN


def test_a_parent_session_is_still_judged_normally():
    row = _one([_turn(900, 900_000, transcript="2c8e4dea-aaf0-4b5b")])
    assert row["kind"] == "interactive"
    assert row["action"] == "Close it now"


# --- one transcript, many contexts ------------------------------------


def test_a_transcript_with_no_reset_is_one_segment():
    from tokendog.hygiene import segment
    turns = [(NOW, 100_000), (NOW, 200_000), (NOW, 300_000)]
    segs = segment(turns)
    assert len(segs) == 1
    assert segs[0]["turns"] == 3 and segs[0]["peak_context"] == 300_000


def test_each_reset_starts_a_new_segment():
    """A reset does not close the file; the file keeps holding new contexts."""
    from tokendog.hygiene import segment
    turns = [(NOW, 500_000), (NOW, 30_000), (NOW, 400_000), (NOW, 20_000)]
    assert len(segment(turns)) == 3


def test_the_last_segment_is_the_one_still_live():
    from tokendog.hygiene import segment
    turns = [(NOW, 900_000), (NOW, 40_000), (NOW, 60_000)]
    live = segment(turns)[-1]
    assert live["turns"] == 2
    assert live["first_context"] == 40_000 and live["last_context"] == 60_000
    assert live["peak_context"] == 60_000, "the discarded 900k is not this context's peak"


def test_segmenting_nothing_yields_nothing():
    from tokendog.hygiene import segment
    assert segment([]) == []


def test_the_verdict_reads_the_live_segment_not_the_lifetime_blend():
    """A session cleared an hour ago is healthy now, whatever it once carried."""
    events = [_turn(5, 900_000), _turn(4, 900_000),
              _turn(3, 30_000), _turn(2, 40_000)]
    row = _one(events)
    assert row["segments"] == 2
    assert row["avg_context"] > OCCUPANCY_WARN, "the lifetime average is still large"
    assert row["current"]["avg_context"] < OCCUPANCY_WARN
    assert row["action"] == "Healthy shape", (
        "judged on the live context, not on contexts already discarded")


def test_a_session_that_never_reset_is_still_judged_on_its_size():
    """Sized into the compact band: over the warn line, under the alarm line.
    Above the alarm the verdict is the blunter 'clear and restart' instead."""
    row = _one([_turn(3, 250_000), _turn(2, 300_000)])
    assert row["segments"] == 1
    assert row["current"]["avg_context"] >= OCCUPANCY_WARN
    assert row["last_context"] < OCCUPANCY_ALARM
    assert row["action"] == "Never reset — compact at 120K"


def test_the_segment_count_is_resets_plus_one():
    row = _one([_turn(5, 900_000), _turn(4, 20_000), _turn(3, 800_000), _turn(2, 10_000)])
    assert row["segments"] == row["resets"] + 1


# --- buckets, weight and burn -----------------------------------------


def _rich(hours_ago, *, transcript="s1", read=0, w5m=0, w1h=0, fresh=0,
          out=0, creation=None, entrypoint="cli", session=None):
    """A turn with the cache buckets set individually."""
    total = creation if creation is not None else w5m + w1h
    return TokenEvent(
        ts=(NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        session_id=session or transcript, transcript_id=transcript,
        runtime=RUNTIME_CLAUDE, event="assistant-turn", source=SOURCE_TRANSCRIPT,
        cache_read_tokens=read, cache_creation_tokens=total,
        cache_creation_5m_tokens=w5m, cache_creation_1h_tokens=w1h,
        input_tokens=fresh, output_tokens=out, project="demo",
        entrypoint=entrypoint)


def test_buckets_split_a_turn_into_what_it_actually_cost():
    from tokendog.hygiene import buckets_of
    b = buckets_of(_rich(1, read=1000, w5m=200, w1h=300, fresh=40, out=7))
    assert b == {"cache_read": 1000, "write_5m": 200, "write_1h": 300,
                 "write_other": 0, "fresh": 40, "output": 7}


def test_cache_creation_with_no_reported_ttl_lands_in_write_other():
    """The runtime does not always report the split. The tokens are real and
    must not vanish, so they are kept and billed at the 5-minute default."""
    from tokendog.hygiene import buckets_of
    b = buckets_of(_rich(1, creation=5000))
    assert b["write_other"] == 5000
    assert b["write_5m"] == b["write_1h"] == 0


def test_write_other_is_never_negative_when_the_split_exceeds_the_total():
    from tokendog.hygiene import buckets_of
    assert buckets_of(_rich(1, creation=100, w5m=80, w1h=80))["write_other"] == 0


def test_weight_prices_a_cache_read_at_a_tenth_and_an_hour_write_at_double():
    from tokendog.hygiene import weighted_input
    assert weighted_input({"cache_read": 1_000_000}) == 100_000
    assert weighted_input({"write_1h": 1_000_000}) == 2_000_000
    assert weighted_input({"write_5m": 1_000_000}) == 1_250_000
    assert weighted_input({"fresh": 1_000_000}) == 1_000_000


def test_weight_excludes_output_so_it_stays_comparable_with_input():
    from tokendog.hygiene import weighted_input
    assert weighted_input({"output": 1_000_000}) == 0


def test_an_identical_total_weighs_twentyfold_more_as_an_hour_write():
    """The distinction the split exists for: two sessions carrying the same
    number of tokens can differ twentyfold on the bill."""
    from tokendog.hygiene import weighted_input
    cheap = weighted_input({"cache_read": 600_000})
    dear = weighted_input({"write_1h": 600_000})
    assert dear == cheap * 20


def test_burn_is_input_per_minute_across_the_scoped_turns():
    events = [_rich(1.0, read=600_000), _rich(0.5, read=600_000)]
    s = _one(events)
    assert s["burn_per_min"] == pytest.approx(1_200_000 / 30.0)


def test_burn_is_none_for_a_single_turn_with_no_elapsed_time():
    """One turn has no span to divide by, and 'infinity per minute' is not a
    figure a reader can act on."""
    assert _one([_rich(1, read=1000)])["burn_per_min"] is None


def test_burn_separates_a_runaway_from_a_grinder_carrying_the_same_total():
    fast = [_rich(1.0, read=500_000, transcript="fast"),
            _rich(0.9, read=500_000, transcript="fast")]
    slow = [_rich(9.0, read=500_000, transcript="slow"),
            _rich(1.0, read=500_000, transcript="slow")]
    rows = {r["transcript"]: r for r in
            hygiene_summary(fast + slow, now=NOW)["sessions"]}
    assert rows["fast"]["input"] == rows["slow"]["input"]
    assert rows["fast"]["burn_per_min"] > rows["slow"]["burn_per_min"] * 10


# --- the entrypoint majority ------------------------------------------


def test_a_mostly_interactive_session_is_not_headless_because_of_a_few_runs():
    """The bug: a sticky OR let 87 sdk-cli records out of 3,014 decide.

    Headless suppresses every age and idle verdict, so the one session actually
    worth closing was told to pre-load its context instead of being cleared.
    """
    events = ([_rich(1, read=600_000, entrypoint="cli") for _ in range(20)]
              + [_rich(1, read=600_000, entrypoint="sdk-cli")])
    s = _one(events)
    assert s["headless"] is False
    assert s["kind"] == "interactive"
    assert s["action"] == "Clear and restart"


def test_a_mostly_headless_session_is_still_headless():
    events = ([_rich(1, read=600_000, entrypoint="sdk-cli") for _ in range(20)]
              + [_rich(1, read=600_000, entrypoint="cli")])
    assert _one(events)["headless"] is True


def test_an_even_split_is_read_as_interactive():
    """The conservative direction: telling someone to reset an exited run is
    harmless, and suppressing the advice on a live one is not."""
    events = [_rich(1, read=600_000, entrypoint="cli"),
              _rich(1, read=600_000, entrypoint="sdk-cli")]
    assert _one(events)["headless"] is False


# --- subagent roll-up -------------------------------------------------


def _parent_and_kids(session="sess-1"):
    return (
        [_rich(1, read=50_000, transcript=session, session=session)]
        + [_rich(1, read=20_000, transcript=f"agent-{i:08x}", session=session)
           for i in range(3)]
    )


def test_a_fanout_is_credited_to_the_session_that_spawned_it():
    """Without this a fan-out reads as a dozen unrelated small sessions."""
    rows = {r["transcript"]: r
            for r in hygiene_summary(_parent_and_kids(), now=NOW)["sessions"]}
    assert rows["sess-1"]["subagents"] == 3
    assert rows["sess-1"]["subagent_input"] == 60_000


def test_the_subagents_keep_their_own_rows_and_their_own_excess():
    """Their contexts were separate; the roll-up only ADDS to the parent."""
    rows = hygiene_summary(_parent_and_kids(), now=NOW)["sessions"]
    assert sum(1 for r in rows if r["subagent"]) == 3
    assert all(r["subagents"] == 0 for r in rows if r["subagent"])


def test_a_subagent_names_its_parent():
    rows = {r["transcript"]: r
            for r in hygiene_summary(_parent_and_kids(), now=NOW)["sessions"]}
    assert rows["agent-00000000"]["parent"] == "sess-1"


def test_the_transcript_named_by_the_session_id_is_the_parent():
    """A resumed session owns several transcripts. Crediting every one of them
    would report the same fan-out several times over."""
    events = _parent_and_kids() + [
        _rich(2, read=10_000, transcript="other-transcript", session="sess-1")
        for _ in range(9)]
    rows = {r["transcript"]: r
            for r in hygiene_summary(events, now=NOW)["sessions"]}
    assert rows["sess-1"]["subagents"] == 3
    assert rows["other-transcript"]["subagents"] == 0


def test_an_orphaned_fanout_still_names_the_parent_it_belongs_to():
    """Inside a narrow window the parent can sit idle while its subagents burn.
    A dozen orphan rows with nothing tying them together is the case the reader
    most needs pointed at."""
    kids = [_rich(1, read=20_000, transcript=f"agent-{i:08x}", session="gone")
            for i in range(3)]
    rows = hygiene_summary(kids, now=NOW)["sessions"]
    assert all(r["parent"] == "gone" for r in rows)


def test_a_session_with_no_subagents_reports_none():
    s = _one([_rich(1, read=50_000)])
    assert (s["subagents"], s["subagent_input"], s["parent"]) == (0, 0, None)


# --- windows ----------------------------------------------------------


def _window(since_hours_ago, until_hours_ago=None):
    from tokendog.window import Window
    return Window(since=NOW - timedelta(hours=since_hours_ago),
                  until=(NOW - timedelta(hours=until_hours_ago)
                         if until_hours_ago is not None else None))


def test_a_window_scopes_which_turns_count():
    events = [_rich(10, read=900_000), _rich(1, read=100_000)]
    s = hygiene_summary(events, now=NOW, window=_window(2))["sessions"][0]
    assert s["turns"] == 1
    assert s["input"] == 100_000


def test_a_window_keeps_the_sessions_own_age_and_idleness():
    """What a session SPENT belongs to the window; how long it has been open
    is a fact about the session. Reporting a three-day-old session as 'open
    for 16 minutes' would hide exactly what this report exists to find."""
    events = [_rich(72, read=300_000), _rich(1, read=300_000)]
    s = hygiene_summary(events, now=NOW, window=_window(2))["sessions"][0]
    assert s["turns"] == 1
    assert s["span_hours"] == pytest.approx(71.0)
    assert s["idle_hours"] == pytest.approx(1.0)


def test_a_session_with_no_turns_in_the_window_is_absent():
    events = [_rich(10, read=900_000)]
    assert hygiene_summary(events, now=NOW,
                           window=_window(2))["totals"]["sessions"] == 0


def test_a_window_is_half_open_at_its_closing_edge():
    events = [_rich(3, read=100_000), _rich(2, read=100_000),
              _rich(1, read=100_000)]
    s = hygiene_summary(events, now=NOW, window=_window(3, 1))["sessions"][0]
    assert s["turns"] == 2


def test_the_window_label_travels_with_the_data():
    d = hygiene_summary([_rich(1, read=1000)], now=NOW, window=_window(3, 1))
    assert d["window"]
    assert d["window_minutes"] == pytest.approx(120.0)


def test_an_open_ended_window_reports_no_length():
    """`--since 19:20` with no `--until` has a label but no span, so nothing
    downstream can divide by it to produce a rate."""
    d = hygiene_summary([_rich(1, read=1000)], now=NOW, window=_window(2))
    assert d["window"] and d["window_minutes"] is None


def test_all_time_reports_no_window():
    d = hygiene_summary([_rich(1, read=1000)], now=NOW)
    assert d["window"] is None and d["window_minutes"] is None


def test_the_older_since_argument_still_scopes_turns():
    events = [_rich(10, read=900_000), _rich(1, read=100_000)]
    s = hygiene_summary(events, now=NOW,
                        since=NOW - timedelta(hours=2))["sessions"][0]
    assert s["turns"] == 1


def test_a_window_ranks_by_what_was_spent_not_by_excess():
    """A session that carried 400k for twenty minutes has no excess if it
    stayed under the line, and ranking a named window by excess buries exactly
    the row the reader opened the window to find."""
    spender = [_rich(1, read=190_000, transcript="spender") for _ in range(6)]
    drifter = [_rich(1, read=OCCUPANCY_WARN + 30_000, transcript="drifter")]
    rows = hygiene_summary(spender + drifter, now=NOW,
                           window=_window(2))["sessions"]
    assert rows[0]["transcript"] == "spender"
    assert rows[0]["excess_tokens"] == 0
    # ...and all-time, the drifter is what is worth fixing, so it leads.
    plain = hygiene_summary(spender + drifter, now=NOW)["sessions"]
    assert plain[0]["transcript"] == "drifter"


def test_window_totals_carry_the_bucket_split():
    events = [_rich(1, read=100_000, w1h=50_000, fresh=10)]
    t = hygiene_summary(events, now=NOW, window=_window(2))["totals"]
    assert t["buckets"]["cache_read"] == 100_000
    assert t["buckets"]["write_1h"] == 50_000
    assert t["weighted_input"] == 10_000 + 100_000 + 10
