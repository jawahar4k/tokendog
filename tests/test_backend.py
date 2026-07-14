import pytest
from tokendog.backend import LocalSQLiteBackend, QueryFilter
from tokendog.event import TokenEvent, RUNTIME_CLAUDE, RUNTIME_GLITCH


def _b():
    return LocalSQLiteBackend(path=":memory:")


def test_group_by_runtime():
    b = _b()
    b.ingest(TokenEvent(ts="2026-07-12T01", session_id="s", runtime=RUNTIME_CLAUDE,
                        event="PostToolUse", input_tokens=10, output_tokens=2))
    b.ingest(TokenEvent(ts="2026-07-12T02", session_id="s", runtime=RUNTIME_GLITCH,
                        event="context-load", input_tokens=100))
    rollup = b.query(QueryFilter(group_by="runtime"))
    by = {r.key: r for r in rollup.rows}
    assert by["glitch"].input_tokens == 100
    assert by["claude-code"].calls == 1
    assert by["claude-code"].est_cost_usd > 0


def test_since_filter():
    b = _b()
    b.ingest(TokenEvent(ts="2026-07-10T00", session_id="s", runtime=RUNTIME_CLAUDE, event="x", input_tokens=1))
    b.ingest(TokenEvent(ts="2026-07-12T00", session_id="s", runtime=RUNTIME_CLAUDE, event="x", input_tokens=5))
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
    b.ingest(TokenEvent(ts="2026-07-13T01", session_id="s", runtime=RUNTIME_CLAUDE,
                        event="PostToolUse", input_tokens=1000, model=haiku_model))
    b.ingest(TokenEvent(ts="2026-07-13T02", session_id="s", runtime=RUNTIME_CLAUDE,
                        event="PostToolUse", input_tokens=1000, model=sonnet_model))
    rollup = b.query(QueryFilter(group_by="runtime"))
    row = rollup.rows[0]
    expected = round(estimate_cost(1000, 0, haiku_model) + estimate_cost(1000, 0, sonnet_model), 6)
    # Ensure haiku and sonnet are priced differently (guard against future pricing collapse)
    assert estimate_cost(1000, 0, haiku_model) != estimate_cost(1000, 0, sonnet_model)
    assert abs(row.est_cost_usd - expected) < 1e-9
