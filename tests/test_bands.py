import pytest

from tokendog.bands import (
    BAND_LABELS,
    LARGE_CONTEXT_THRESHOLD,
    band_for,
    band_summary,
    context_tokens,
    percentile,
)
from tokendog.event import TokenEvent, RUNTIME_CLAUDE, SOURCE_HOOK, SOURCE_TRANSCRIPT


def _turn(ctx_read=0, ctx_write=0, fresh=0, session="s", **kw):
    return TokenEvent(
        ts="2026-09-01T10:00:00Z", session_id=session, runtime=RUNTIME_CLAUDE,
        event="assistant-turn", source=SOURCE_TRANSCRIPT,
        input_tokens=fresh, cache_read_tokens=ctx_read,
        cache_creation_tokens=ctx_write, **kw)


# --- what counts as context --------------------------------------------


def test_context_is_read_plus_write_plus_fresh_input():
    assert context_tokens(input_tokens=5, cache_read_tokens=100, cache_creation_tokens=20) == 125


def test_output_is_not_context():
    """Output is what came back, not what had to be carried."""
    e = _turn(ctx_read=100, fresh=5)
    e.output_tokens = 9999
    s = band_summary([e])
    assert s["total_context_tokens"] == 105


# --- band edges ---------------------------------------------------------


@pytest.mark.parametrize("ctx,expected", [
    (0, "<50k"),
    (49_999, "<50k"),
    (50_000, "50-100k"),
    (99_999, "50-100k"),
    (100_000, "100-150k"),
    (150_000, "150-200k"),
    (199_999, "150-200k"),
    (200_000, "200-400k"),
    (399_999, "200-400k"),
    (400_000, "400k+"),
    (999_358, "400k+"),
])
def test_band_edges_are_lower_inclusive(ctx, expected):
    assert band_for(ctx) == expected


def test_negative_and_none_are_smallest_band():
    assert band_for(-1) == "<50k"
    assert band_for(None) == "<50k"


# --- histogram ----------------------------------------------------------


def test_every_band_is_present_even_when_empty():
    s = band_summary([_turn(ctx_read=10)])
    assert [r["band"] for r in s["rows"]] == list(BAND_LABELS)


def test_turns_and_tokens_are_counted_per_band():
    s = band_summary([
        _turn(ctx_read=10_000),
        _turn(ctx_read=60_000),
        _turn(ctx_read=70_000),
    ])
    by = {r["band"]: r for r in s["rows"]}
    assert by["<50k"]["turns"] == 1 and by["<50k"]["context_tokens"] == 10_000
    assert by["50-100k"]["turns"] == 2 and by["50-100k"]["context_tokens"] == 130_000
    assert s["turns"] == 3 and s["total_context_tokens"] == 140_000


def test_percentages_sum_to_100():
    s = band_summary([_turn(ctx_read=10_000), _turn(ctx_read=500_000)])
    assert abs(sum(r["pct_turns"] for r in s["rows"]) - 100.0) < 1e-9
    assert abs(sum(r["pct_tokens"] for r in s["rows"]) - 100.0) < 1e-9


def test_empty_input_does_not_divide_by_zero():
    s = band_summary([])
    assert s["turns"] == 0 and s["total_context_tokens"] == 0
    assert all(r["pct_turns"] == 0.0 for r in s["rows"])
    assert s["peak_context"]["max"] == 0
    assert s["concentration"]["pct_tokens"] == 0.0


# --- hook events have no context of their own --------------------------


def test_hook_events_are_excluded():
    """A hook event measures a tool payload, not a turn. Counting it would
    invent turns that never happened and skew every percentage."""
    hook = TokenEvent(ts="2026-09-01T10:00:00Z", session_id="s",
                      runtime=RUNTIME_CLAUDE, event="PostToolUse",
                      source=SOURCE_HOOK, tool_payload_tokens=90_000)
    s = band_summary([hook, _turn(ctx_read=10_000)])
    assert s["turns"] == 1
    assert s["total_context_tokens"] == 10_000


# --- the headline -------------------------------------------------------


def test_concentration_measures_the_large_tail():
    # 3 small turns, 1 huge one: 25% of turns carry the large majority.
    s = band_summary([
        _turn(ctx_read=10_000), _turn(ctx_read=10_000), _turn(ctx_read=10_000),
        _turn(ctx_read=970_000),
    ])
    c = s["concentration"]
    assert c["threshold"] == LARGE_CONTEXT_THRESHOLD
    assert c["turns"] == 1
    assert abs(c["pct_turns"] - 25.0) < 1e-9
    assert c["pct_tokens"] > 95.0


# --- session peaks ------------------------------------------------------


def test_peak_context_is_max_per_transcript():
    s = band_summary([
        _turn(ctx_read=10_000, transcript_id="a"),
        _turn(ctx_read=300_000, transcript_id="a"),
        _turn(ctx_read=50_000, transcript_id="b"),
    ])
    peak = s["peak_context"]
    assert peak["count"] == 2
    assert peak["max"] == 300_000


def test_peaks_key_on_transcript_not_session():
    """A resumed session and its subagent transcripts share one sessionId.
    Keying peaks on session_id would merge separate contexts into one."""
    s = band_summary([
        _turn(ctx_read=100_000, session="shared", transcript_id="t1"),
        _turn(ctx_read=900_000, session="shared", transcript_id="t2"),
    ])
    assert s["peak_context"]["count"] == 2
    assert s["peak_context"]["p50"] == 100_000


def test_percentile_nearest_rank():
    assert percentile([], 50) == 0
    assert percentile([5], 50) == 5
    assert percentile([1, 2, 3, 4], 50) == 2
    assert percentile([1, 2, 3, 4], 100) == 4
    assert percentile([1, 2, 3, 4], 0) == 1
    assert percentile(list(range(1, 11)), 90) == 9


# --- roll-up dimension + report wiring ----------------------------------

def test_band_is_a_rollup_dimension():
    from tokendog.backend import LocalSQLiteBackend, QueryFilter
    b = LocalSQLiteBackend(path=":memory:")
    b.ingest(_turn(ctx_read=10_000))
    b.ingest(_turn(ctx_read=500_000))
    rows = {r.key: r for r in b.query(QueryFilter(group_by="band")).rows}
    assert rows["<50k"].calls == 1
    assert rows["400k+"].calls == 1


def test_band_derived_on_construction_only_for_turns():
    turn = _turn(ctx_read=250_000)
    assert turn.band == "200-400k" and turn.context_tokens == 250_000 and turn.is_turn
    hook = TokenEvent(ts="t", session_id="s", runtime=RUNTIME_CLAUDE,
                      event="PostToolUse", source=SOURCE_HOOK, tool_payload_tokens=99)
    assert hook.band is None and not hook.is_turn


def test_band_survives_a_json_roundtrip():
    e = _turn(ctx_read=250_000)
    back = TokenEvent.from_json(e.to_json())
    assert back.band == "200-400k" and back.context_tokens == 250_000


def test_format_bands_reports_the_headline():
    from tokendog import report
    s = band_summary([
        _turn(ctx_read=10_000), _turn(ctx_read=10_000), _turn(ctx_read=10_000),
        _turn(ctx_read=970_000),
    ])
    out = report.format_bands(s)
    assert "| Band |" in out
    assert "25.0% of turns carry" in out
    assert "Peak context per transcript" in out
    assert "400k+" in out


def test_format_bands_handles_no_data():
    from tokendog import report
    out = report.format_bands(band_summary([]))
    assert "No metered turns" in out


def test_bands_cli_runs(capsys):
    from tokendog import report
    assert report.main(["bands"]) == 0
    assert "TokenDog context bands" in capsys.readouterr().out
