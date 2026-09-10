from datetime import datetime, timedelta, timezone

import pytest

from tokendog.cold_start import (
    MIN_RAMP_TOKENS,
    MIN_RUNS,
    RAMP_PEAK_SHARE,
    cold_start_summary,
    ramp,
)
from tokendog.event import (
    RUNTIME_CLAUDE,
    SOURCE_HOOK,
    SOURCE_TRANSCRIPT,
    TokenEvent,
)

T0 = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def _turn(minutes, ctx, *, transcript="r1", project="demo",
          entrypoint="sdk-cli", session=None):
    return TokenEvent(
        ts=(T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        session_id=session or transcript, transcript_id=transcript,
        runtime=RUNTIME_CLAUDE, event="assistant-turn", source=SOURCE_TRANSCRIPT,
        cache_read_tokens=ctx, output_tokens=50, project=project,
        entrypoint=entrypoint)


def _run(transcript, contexts, *, project="demo", entrypoint="sdk-cli"):
    return [_turn(i, c, transcript=transcript, project=project,
                  entrypoint=entrypoint) for i, c in enumerate(contexts)]


def _hook(session, filename):
    return TokenEvent(ts=T0.isoformat(), session_id=session, runtime=RUNTIME_CLAUDE,
                      event="tool", source=SOURCE_HOOK, tool_payload_tokens=10,
                      file=filename, tool="Read")


# --- the ramp ---------------------------------------------------------


def test_ramp_ends_when_occupancy_reaches_half_the_peak():
    turns, tokens = ramp([10, 20, 60, 100, 100])
    assert turns == 3                     # the crossing turn is part of the climb
    assert tokens == 10 + 20 + 60


def test_the_crossing_turn_is_included_because_it_is_the_read():
    turns, _ = ramp([100, 100])
    assert turns == 1


def test_a_steadily_climbing_run_crosses_half_peak_partway():
    """Peak is 30, so half-peak is 20 and the second turn is the crossing.

    The peak is always a member of the series, so the threshold is always
    reached — the ramp is a prefix of the run, never the whole of it unless the
    very first turn is already the peak.
    """
    turns, tokens = ramp([10, 20, 30])
    assert (turns, tokens) == (2, 30)


def test_a_run_whose_first_turn_is_its_peak_has_a_one_turn_ramp():
    assert ramp([500_000, 100_000, 90_000]) == (1, 500_000)


def test_ramp_of_nothing_is_zero():
    assert ramp([]) == (0, 0)


def test_ramp_scales_with_the_run_rather_than_a_fixed_turn_count():
    """A run reading two files and one reading two hundred both get a fair ramp."""
    small = ramp([1_000, 5_000, 10_000])
    large = ramp([100_000, 500_000, 1_000_000])
    assert small[0] == large[0]


# --- grouping ---------------------------------------------------------


def test_a_lone_run_is_never_duplication():
    s = cold_start_summary(_run("r1", [10_000, 200_000, 400_000]))
    assert s["totals"]["projects"] == 0
    assert s["totals"]["headless_runs_seen"] == 1


def test_two_runs_on_one_project_are_duplication():
    events = _run("r1", [50_000, 300_000, 600_000]) + _run("r2", [50_000, 300_000, 600_000])
    s = cold_start_summary(events)
    assert s["totals"]["projects"] == 1
    g = s["groups"][0]
    assert g["runs"] == 2
    assert g["project"] == "demo"


def test_duplication_is_the_group_ramp_minus_the_cheapest_one():
    """One run genuinely has to discover; only the rest are charged."""
    events = _run("r1", [100_000, 600_000]) + _run("r2", [100_000, 600_000])
    g = cold_start_summary(events)["groups"][0]
    ramps = [r["ramp_tokens"] for r in g["runs_detail"]]
    assert g["duplicated_tokens"] == sum(ramps) - min(ramps)


def test_duplication_never_exceeds_the_group_ramp():
    events = (_run("r1", [100_000, 600_000]) + _run("r2", [90_000, 550_000])
              + _run("r3", [120_000, 700_000]))
    g = cold_start_summary(events)["groups"][0]
    assert g["duplicated_tokens"] < g["ramp_tokens"]


def test_runs_on_different_projects_are_not_each_others_duplication():
    events = (_run("r1", [100_000, 600_000], project="alpha")
              + _run("r2", [100_000, 600_000], project="beta"))
    assert cold_start_summary(events)["totals"]["projects"] == 0


def test_trivial_discovery_is_not_worth_naming():
    tiny = [1_000, 2_000]
    events = _run("r1", tiny) + _run("r2", tiny)
    s = cold_start_summary(events)
    assert s["totals"]["projects"] == 0
    assert s["thresholds"]["min_ramp_tokens"] == MIN_RAMP_TOKENS


# --- who counts -------------------------------------------------------


def test_interactive_sessions_are_out_of_scope():
    """An interactive session's problem is carrying too much, not too little."""
    events = (_run("a", [100_000, 600_000], entrypoint="cli")
              + _run("b", [100_000, 600_000], entrypoint="cli"))
    s = cold_start_summary(events)
    assert s["totals"]["headless_runs_seen"] == 0
    assert s["groups"] == []


def test_a_mixed_project_only_counts_its_headless_runs():
    events = (_run("h1", [100_000, 600_000]) + _run("h2", [100_000, 600_000])
              + _run("i1", [100_000, 600_000], entrypoint="cli"))
    s = cold_start_summary(events)
    assert s["groups"][0]["runs"] == 2


def test_non_turns_do_not_become_runs():
    events = [_hook("r1", "a.ts"), _hook("r2", "b.ts")]
    assert cold_start_summary(events)["totals"]["headless_runs_seen"] == 0


def test_transcripts_are_separate_runs_even_sharing_a_session_id():
    events = (_run("t1", [100_000, 600_000]) + _run("t2", [100_000, 600_000]))
    for e in events:
        e.session_id = "same"
    assert cold_start_summary(events)["groups"][0]["runs"] == 2


def test_turns_arriving_out_of_order_are_sorted_before_the_ramp():
    a = _run("r1", [600_000, 100_000])          # written newest-first
    a[0].ts = (T0 + timedelta(minutes=9)).isoformat().replace("+00:00", "Z")
    a[1].ts = T0.isoformat().replace("+00:00", "Z")
    b = _run("r2", [100_000, 600_000])
    g = cold_start_summary(a + b)["groups"][0]
    ramps = sorted(r["ramp_tokens"] for r in g["runs_detail"])
    assert ramps[0] == ramps[1], "both runs climbed the same ramp"


# --- corroboration from hook telemetry -------------------------------


def test_files_touched_by_several_runs_are_named():
    events = (_run("r1", [100_000, 600_000]) + _run("r2", [100_000, 600_000])
              + [_hook("r1", "pipeline.ts"), _hook("r1", "only-r1.ts"),
                 _hook("r2", "pipeline.ts")])
    g = cold_start_summary(events)["groups"][0]
    assert g["shared_file_count"] == 1
    assert g["shared_files"][0] == {"file": "pipeline.ts", "runs": 2}


def test_a_file_only_one_run_touched_is_not_shared():
    events = (_run("r1", [100_000, 600_000]) + _run("r2", [100_000, 600_000])
              + [_hook("r1", "solo.ts")])
    assert cold_start_summary(events)["groups"][0]["shared_file_count"] == 0


def test_without_hook_telemetry_the_group_still_reports():
    """The ramp stands on its own; shared files are corroboration, not a gate."""
    events = _run("r1", [100_000, 600_000]) + _run("r2", [100_000, 600_000])
    g = cold_start_summary(events)["groups"][0]
    assert g["shared_files"] == []
    assert g["duplicated_tokens"] > 0


# --- contract ---------------------------------------------------------


def test_empty_input_summarises_to_zero_rather_than_raising():
    s = cold_start_summary([])
    assert s["totals"]["projects"] == 0
    assert s["groups"] == []


def test_thresholds_are_published_with_the_result():
    th = cold_start_summary([])["thresholds"]
    assert th["min_runs"] == MIN_RUNS
    assert th["ramp_peak_share"] == RAMP_PEAK_SHARE


def test_summary_is_json_serialisable():
    import json
    events = _run("r1", [100_000, 600_000]) + _run("r2", [100_000, 600_000])
    s = cold_start_summary(events)
    assert json.loads(json.dumps(s))["totals"]["projects"] == 1


def test_the_dashboard_reads_headless_from_the_event_not_a_file_scan():
    """The head-scan helper is gone; the schema carries it."""
    from tokendog import views
    assert not hasattr(views, "transcript_entrypoints")
    assert not hasattr(views, "is_headless")


def test_a_run_that_exited_immediately_is_not_a_run():
    """One usage block of zeroes must not absorb the free-discovery credit."""
    dud = _turn(0, 0, transcript="dud")
    events = (_run("r1", [100_000, 600_000]) + _run("r2", [100_000, 600_000]) + [dud])
    s = cold_start_summary(events)
    g = s["groups"][0]
    assert g["runs"] == 2
    assert s["totals"]["headless_runs_seen"] == 2
    assert g["duplicated_tokens"] < g["ramp_tokens"], (
        "the cheapest real ramp must still be credited")
