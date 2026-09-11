import pytest
from tokendog.backend import LocalSQLiteBackend, QueryFilter, event_cost
from tokendog.event import (
    TokenEvent, RUNTIME_CLAUDE, RUNTIME_GLITCH, SOURCE_HOOK, SOURCE_TRANSCRIPT,
)


def _b():
    return LocalSQLiteBackend(path=":memory:")


def _turn(**kw):
    """An authoritative metered turn (the only thing that gets priced)."""
    kw.setdefault("ts", "2026-07-12T01")
    kw.setdefault("session_id", "s")
    kw.setdefault("runtime", RUNTIME_CLAUDE)
    kw.setdefault("event", "assistant-turn")
    kw.setdefault("source", SOURCE_TRANSCRIPT)
    return TokenEvent(**kw)


def test_group_by_runtime():
    b = _b()
    b.ingest(_turn(input_tokens=10, output_tokens=2))
    b.ingest(_turn(ts="2026-07-12T02", runtime=RUNTIME_GLITCH, event="context-load",
                   source="glitch", input_tokens=100))
    rollup = b.query(QueryFilter(group_by="runtime"))
    by = {r.key: r for r in rollup.rows}
    assert by["glitch"].input_tokens == 100
    assert by["claude-code"].calls == 1
    assert by["claude-code"].est_cost_usd > 0


def test_since_filter():
    b = _b()
    b.ingest(_turn(ts="2026-07-10T00", event="x", input_tokens=1))
    b.ingest(_turn(ts="2026-07-12T00", event="x", input_tokens=5))
    rollup = b.query(QueryFilter(group_by="runtime", since="2026-07-11"))
    assert rollup.rows[0].input_tokens == 5


def test_invalid_group_by_rejected():
    with pytest.raises(ValueError):
        _b().query(QueryFilter(group_by="drop table"))


def test_mixed_model_cost_uses_per_event_pricing():
    from tokendog.pricing import estimate_cost
    b = _b()
    haiku_model = "claude-haiku-4-5"
    sonnet_model = "claude-sonnet-4-6"
    b.ingest(_turn(ts="2026-07-13T01", input_tokens=1000, model=haiku_model))
    b.ingest(_turn(ts="2026-07-13T02", input_tokens=1000, model=sonnet_model))
    rollup = b.query(QueryFilter(group_by="runtime"))
    row = rollup.rows[0]
    expected = round(estimate_cost(1000, 0, haiku_model) + estimate_cost(1000, 0, sonnet_model), 6)
    # Ensure haiku and sonnet are priced differently (guard against future pricing collapse)
    assert estimate_cost(1000, 0, haiku_model) != estimate_cost(1000, 0, sonnet_model)
    assert abs(row.est_cost_usd - expected) < 1e-9


# --- cache is the majority of a real bill; it must reach the rollup -----


def test_cache_tokens_are_priced():
    """Regression: cache buckets used to be stored but never priced, so ~85%
    of a real agent bill silently contributed $0."""
    b = _b()
    b.ingest(_turn(model="claude-opus-5", cache_read_tokens=1_000_000,
                   cache_creation_tokens=1_000_000, cache_creation_1h_tokens=1_000_000))
    row = b.query(QueryFilter(group_by="runtime")).rows[0]
    assert row.est_cost_usd > 0


def test_cache_buckets_surface_in_the_rollup():
    b = _b()
    b.ingest(_turn(cache_read_tokens=500, cache_creation_tokens=300,
                   cache_creation_5m_tokens=300))
    row = b.query(QueryFilter(group_by="runtime")).rows[0]
    assert row.cache_read_tokens == 500
    assert row.cache_creation_tokens == 300
    assert row.total_tokens == 800


def test_1h_cache_write_costs_more_than_5m_end_to_end():
    a, b = _b(), _b()
    a.ingest(_turn(model="claude-opus-5", cache_creation_tokens=1_000_000,
                   cache_creation_5m_tokens=1_000_000))
    b.ingest(_turn(model="claude-opus-5", cache_creation_tokens=1_000_000,
                   cache_creation_1h_tokens=1_000_000))
    cost_5m = a.query(QueryFilter(group_by="runtime")).rows[0].est_cost_usd
    cost_1h = b.query(QueryFilter(group_by="runtime")).rows[0].est_cost_usd
    assert cost_1h > cost_5m


# --- hook events are volume, not billing -------------------------------


def test_hook_events_are_never_priced():
    """A tool payload observed by a hook is already billed by the turn that
    carries it. Pricing it again double-counts; pricing it as OUTPUT (the old
    behaviour) overstates it by the output/input rate ratio."""
    e = TokenEvent(ts="2026-07-12T01", session_id="s", runtime=RUNTIME_CLAUDE,
                   event="PostToolUse", source=SOURCE_HOOK,
                   tool_payload_tokens=1_000_000, model="claude-opus-5")
    assert event_cost(e) == 0.0


def test_hook_events_still_recorded_for_attribution():
    b = _b()
    b.ingest(TokenEvent(ts="2026-07-12T01", session_id="s", runtime=RUNTIME_CLAUDE,
                        event="PostToolUse", source=SOURCE_HOOK, tool="Read",
                        tool_payload_tokens=1234))
    row = b.query(QueryFilter(group_by="tool")).rows[0]
    assert row.key == "Read"
    assert row.calls == 1
    assert row.est_cost_usd == 0.0


def test_the_backend_makes_the_caller_choose_where_data_lives():
    """No implicit on-disk default, because nothing here wants one.

    Every report rebuilds into `:memory:` from the transcripts and the sink in
    about a second, so there is no persisted store — and a default that silently
    named `~/.tokendog/cost.db` described a file that never existed. Worse, a
    half-written or stale copy of it would answer queries as confidently as a
    correct one. The caller now says "ephemeral" or names a file.
    """
    with pytest.raises(TypeError):
        LocalSQLiteBackend()


def test_config_does_not_advertise_a_cost_db_that_is_never_written():
    import tokendog.config as config
    assert not hasattr(config, "db_path")
