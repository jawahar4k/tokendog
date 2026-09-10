import json
from datetime import datetime, timedelta, timezone

import pytest

from tokendog.views import (
    AGE_LONG_LIVED_H,
    IDLE_STALE_H,
    OCCUPANCY_ALARM,
    OCCUPANCY_WARN,
    RESUME_GAP_MIN,
    VIEW_BANDS,
    ledger_data,
    view_band_for,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _rec(ts, ctx, out=100, cwd="/somewhere/projects/demo", entrypoint=None):
    """One assistant transcript record carrying `ctx` tokens of context."""
    rec = {
        "type": "assistant",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "cwd": cwd,
        "message": {
            "model": "test-model",
            "usage": {"input_tokens": 0, "output_tokens": out,
                      "cache_read_input_tokens": ctx, "cache_creation_input_tokens": 0},
        },
    }
    if entrypoint:
        rec["entrypoint"] = entrypoint
    return rec


def _write(root, name, records):
    d = root / "proj"
    d.mkdir(exist_ok=True)
    f = d / f"{name}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return f


# --- band edges ---------------------------------------------------------


@pytest.mark.parametrize("ctx,expected", [
    (0, "<100K"), (99_999, "<100K"), (100_000, "100-200K"), (199_999, "100-200K"),
    (200_000, "200-400K"), (399_999, "200-400K"), (400_000, "400-700K"),
    (699_999, "400-700K"), (700_000, "700K+"), (999_999, "700K+"),
])
def test_view_band_edges(ctx, expected):
    assert view_band_for(ctx) == expected


def test_view_bands_are_contiguous_and_open_ended():
    """Gaps would silently drop turns from every percentage on the page."""
    edges = [(lo, hi) for _, lo, hi in VIEW_BANDS]
    assert edges[0][0] == 0
    assert edges[-1][1] is None
    for (_, upper), (lower, _) in zip(edges, edges[1:]):
        assert upper == lower


def test_warn_threshold_is_not_restated():
    """It is imported from bands so the page and the CLI cannot disagree."""
    from tokendog.bands import LARGE_CONTEXT_THRESHOLD
    assert OCCUPANCY_WARN == LARGE_CONTEXT_THRESHOLD


# --- headline ----------------------------------------------------------


def test_headline_counts_context_not_output(tmp_path):
    _write(tmp_path, "s1", [_rec(NOW - timedelta(hours=1), 120_000, out=9_999)])
    d = ledger_data(tmp_path, now=NOW)
    assert d["headline"]["input"] == 120_000
    assert d["headline"]["output"] == 9_999
    assert d["headline"]["turns"] == 1


def test_turns_outside_the_window_are_excluded(tmp_path):
    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(days=30), 500_000),   # old
        _rec(NOW - timedelta(hours=2), 100_000),   # in window
    ])
    d = ledger_data(tmp_path, now=NOW, window_days=7)
    assert d["headline"]["turns"] == 1
    assert d["headline"]["input"] == 100_000


def test_concentration_splits_at_the_alarm_threshold(tmp_path):
    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(hours=3), 50_000),
        _rec(NOW - timedelta(hours=2), OCCUPANCY_ALARM),
    ])
    d = ledger_data(tmp_path, now=NOW)
    assert d["headline"]["heavy"]["turns"] == 1
    assert d["headline"]["heavy"]["tokens"] == OCCUPANCY_ALARM
    assert d["headline"]["heavy"]["pct_turns"] == 50.0


def test_empty_tree_reports_zeroes_rather_than_raising(tmp_path):
    d = ledger_data(tmp_path, now=NOW)
    assert d["headline"]["turns"] == 0
    assert d["headline"]["ratio"] is None
    assert d["sessions"] == []
    assert d["findings"] == []


def test_unparseable_timestamp_costs_only_its_own_turn(tmp_path):
    bad = _rec(NOW - timedelta(hours=1), 10_000)
    bad["timestamp"] = "not-a-date"
    _write(tmp_path, "s1", [bad, _rec(NOW - timedelta(hours=1), 30_000)])
    d = ledger_data(tmp_path, now=NOW)
    assert d["headline"]["turns"] == 1
    assert d["headline"]["input"] == 30_000


# --- sessions ----------------------------------------------------------


def test_session_rollup_keys_on_transcript(tmp_path):
    """Two transcripts are two contexts even when they share a session id."""
    shared = {"sessionId": "same-session"}
    _write(tmp_path, "t1", [{**_rec(NOW - timedelta(hours=2), 100_000), **shared}])
    _write(tmp_path, "t2", [{**_rec(NOW - timedelta(hours=1), 200_000), **shared}])
    d = ledger_data(tmp_path, now=NOW)
    assert d["session_count"] == 2
    assert {s["peak_context"] for s in d["sessions"]} == {100_000, 200_000}


def test_span_and_idle_are_measured_separately(tmp_path):
    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(hours=10), 10_000),
        _rec(NOW - timedelta(hours=4), 20_000),
    ])
    s = ledger_data(tmp_path, now=NOW)["sessions"][0]
    assert s["span_hours"] == pytest.approx(6.0, abs=0.1)
    assert s["idle_hours"] == pytest.approx(4.0, abs=0.1)


def test_loaded_and_idle_interactive_session_is_critical(tmp_path):
    _write(tmp_path, "s1", [_rec(NOW - timedelta(hours=IDLE_STALE_H + 2), 500_000)])
    s = ledger_data(tmp_path, now=NOW)["sessions"][0]
    assert s["severity"] == "crit"
    assert s["action"] == "Close it now"


def test_small_recent_session_is_healthy(tmp_path):
    _write(tmp_path, "s1", [_rec(NOW - timedelta(minutes=10), 40_000)])
    s = ledger_data(tmp_path, now=NOW)["sessions"][0]
    assert (s["severity"], s["action"]) == ("ok", "Healthy shape")


def test_long_lived_session_is_flagged_by_span(tmp_path):
    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(hours=AGE_LONG_LIVED_H + 5), 50_000),
        _rec(NOW - timedelta(minutes=5), 60_000),
    ])
    d = ledger_data(tmp_path, now=NOW)
    assert d["sessions"][0]["action"] == "Retire the session"
    assert any(f["kind"] == "long-lived" for f in d["findings"])


# --- headless runs -----------------------------------------------------


def test_exited_headless_run_is_never_told_to_close(tmp_path):
    """It holds no window, so 'close it' would be noise — the fix is upstream."""
    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(hours=IDLE_STALE_H + 5), 300_000, entrypoint="sdk-cli"),
    ])
    d = ledger_data(tmp_path, now=NOW)
    s = d["sessions"][0]
    assert s["headless"] is True
    assert s["action"] == "Pre-load shared context"
    assert not any(f["kind"] == "stale-open" for f in d["findings"])


def test_repeated_headless_runs_on_one_project_are_duplication(tmp_path):
    for name in ("r1", "r2", "r3"):
        _write(tmp_path, name, [
            _rec(NOW - timedelta(hours=2), 250_000, entrypoint="sdk-cli"),
        ])
    d = ledger_data(tmp_path, now=NOW)
    dup = [f for f in d["findings"] if f["kind"] == "cold-start-dup"]
    assert len(dup) == 1
    assert dup[0]["runs"] == 3
    assert dup[0]["input"] == 750_000


def test_a_single_headless_run_is_not_duplication(tmp_path):
    _write(tmp_path, "r1", [_rec(NOW - timedelta(hours=2), 250_000, entrypoint="sdk-cli")])
    d = ledger_data(tmp_path, now=NOW)
    assert not any(f["kind"] == "cold-start-dup" for f in d["findings"])


# --- resume at the wall ------------------------------------------------


def test_resume_needs_both_a_gap_and_a_large_window(tmp_path):
    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(hours=5), 500_000),
        # long gap but small window — not a resume-at-the-wall
        _rec(NOW - timedelta(hours=3), 50_000),
        # large window but no gap
        _rec(NOW - timedelta(hours=3, minutes=-1), 600_000),
    ])
    d = ledger_data(tmp_path, now=NOW)
    assert [f for f in d["findings"] if f["kind"] == "limit-resume"] == []


def test_resume_is_recorded_with_its_context_and_gap(tmp_path):
    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(hours=6), 400_000),
        _rec(NOW - timedelta(hours=6) + timedelta(minutes=RESUME_GAP_MIN + 5), 650_000),
    ])
    found = [f for f in ledger_data(tmp_path, now=NOW)["findings"]
             if f["kind"] == "limit-resume"]
    assert len(found) == 1
    assert found[0]["context"] == 650_000
    assert found[0]["gap_minutes"] == RESUME_GAP_MIN + 5


# --- contract ----------------------------------------------------------


def test_payload_is_json_serialisable(tmp_path):
    """The view-model is also the export contract, so it must round-trip."""
    _write(tmp_path, "s1", [_rec(NOW - timedelta(hours=1), 220_000)])
    d = ledger_data(tmp_path, now=NOW)
    assert json.loads(json.dumps(d))["headline"]["input"] == 220_000


def test_thresholds_are_published_with_the_data(tmp_path):
    """A reader can only judge a severity if the cut that produced it is stated."""
    d = ledger_data(tmp_path, now=NOW)
    assert d["thresholds"]["occupancy_warn"] == OCCUPANCY_WARN
    assert d["thresholds"]["occupancy_alarm"] == OCCUPANCY_ALARM


def test_project_filter_restricts_every_figure(tmp_path):
    _write(tmp_path, "a", [_rec(NOW - timedelta(hours=1), 100_000, cwd="/x/alpha")])
    _write(tmp_path, "b", [_rec(NOW - timedelta(hours=1), 900_000, cwd="/x/beta")])
    d = ledger_data(tmp_path, project="alpha", now=NOW)
    assert d["headline"]["input"] == 100_000
    assert [s["project"] for s in d["sessions"]] == ["alpha"]


# --- one definition of a finding, shared with the detector -------------


def test_the_dashboard_raises_the_same_findings_as_the_detector(tmp_path):
    """Both call hygiene.session_findings, so the two cannot drift apart.

    They did: the copy in views had no no-live-window guard on `long-lived`, so
    the dashboard reported headless runs and subagent transcripts as long-lived
    while `tokendog hygiene` correctly did not.
    """
    from tokendog.hygiene import hygiene_summary
    from tokendog.transcripts import read_transcripts

    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(hours=200), 500_000),
        _rec(NOW - timedelta(hours=1), 900_000),
    ])
    _write(tmp_path, "agent-abc123", [
        _rec(NOW - timedelta(hours=200), 500_000),
        _rec(NOW - timedelta(hours=1), 900_000),
    ])

    dash = ledger_data(tmp_path, now=NOW)
    detector = hygiene_summary(read_transcripts(tmp_path), now=NOW)

    def kinds(findings):
        return sorted((f["kind"], f["session"]) for f in findings
                      if f["kind"] != "limit-resume")

    assert kinds(dash["findings"]) == kinds(detector["findings"])


def test_a_subagent_transcript_is_not_called_long_lived_on_the_dashboard(tmp_path):
    _write(tmp_path, "agent-abc123", [
        _rec(NOW - timedelta(hours=500), 300_000),
        _rec(NOW - timedelta(hours=1), 300_000),
    ])
    d = ledger_data(tmp_path, now=NOW)
    assert not any(f["kind"] in ("long-lived", "stale-open") for f in d["findings"])


# --- previous window, ranges and recommendations -----------------------


def test_the_previous_window_of_the_same_length_is_measured(tmp_path):
    """A number with nothing to compare to cannot say whether things improved."""
    _write(tmp_path, "s1", [
        _rec(NOW - timedelta(days=10), 100_000),   # previous 7d window
        _rec(NOW - timedelta(days=1), 300_000),    # current
    ])
    d = ledger_data(tmp_path, now=NOW, window_days=7, include_surface=False)
    assert d["headline"]["input"] == 300_000
    assert d["previous"]["input"] == 100_000
    assert d["change"]["input"] == 200.0


def test_change_is_none_when_there_is_no_baseline(tmp_path):
    _write(tmp_path, "s1", [_rec(NOW - timedelta(hours=1), 100_000)])
    d = ledger_data(tmp_path, now=NOW, include_surface=False)
    assert d["previous"]["input"] == 0
    assert d["change"]["input"] is None


def test_the_trend_always_covers_both_windows(tmp_path):
    """A 90-day range with a 14-day trend would chart less than it compares."""
    _write(tmp_path, "s1", [_rec(NOW - timedelta(days=100), 10_000),
                            _rec(NOW - timedelta(days=1), 10_000)])
    d = ledger_data(tmp_path, now=NOW, window_days=90, include_surface=False)
    assert len(d["daily"]) >= 2


def test_recommendations_are_ranked_by_tokens(tmp_path):
    _write(tmp_path, "big", [
        _rec(NOW - timedelta(hours=40), 900_000),
        _rec(NOW - timedelta(hours=30), 900_000),
    ])
    _write(tmp_path, "small", [_rec(NOW - timedelta(hours=20), 200_000)])
    recs = ledger_data(tmp_path, now=NOW, include_surface=False)["recommendations"]
    tokens = [r["tokens"] for r in recs]
    assert tokens == sorted(tokens, reverse=True)


def test_every_recommendation_names_an_action_and_its_effort(tmp_path):
    _write(tmp_path, "s1", [_rec(NOW - timedelta(hours=40), 900_000)])
    for r in ledger_data(tmp_path, now=NOW, include_surface=False)["recommendations"]:
        assert r["command"] and r["effort"] and r["why"]


def test_no_findings_means_no_recommendations(tmp_path):
    _write(tmp_path, "s1", [_rec(NOW - timedelta(minutes=5), 20_000)])
    assert ledger_data(tmp_path, now=NOW, include_surface=False)["recommendations"] == []


def test_surface_can_be_left_out_for_speed(tmp_path):
    d = ledger_data(tmp_path, now=NOW, include_surface=False)
    assert d["surface"] is None
