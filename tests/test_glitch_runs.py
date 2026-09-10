"""Glitch pipeline attribution from .glitch/runs/*.json."""
import json
from pathlib import Path

import pytest

from tokendog.glitch_runs import _pipeline_name, read_run_file, stage_rows


def _run(dirpath, run_id, name, stages):
    """stages: {stage_name: (session_id, cost, tin, tout)}"""
    d = dirpath / ".glitch" / "runs"; d.mkdir(parents=True, exist_ok=True)
    obj = {"run_id": run_id, "name": name, "project_dir": str(dirpath),
           "stages": {sn: {"session_id": sid, "cost_usd": c,
                           "tokens_input": tin, "tokens_output": tout}
                      for sn, (sid, c, tin, tout) in stages.items()}}
    (d / f"{run_id}.json").write_text(json.dumps(obj), encoding="utf-8")
    return d / f"{run_id}.json"


def test_pipeline_name_strips_the_run_timestamp():
    assert _pipeline_name({}, "chunk-embed-store-20260909-072904617") == "chunk-embed-store"
    assert _pipeline_name({"name": "sparse-hybrid-20260907-104354985"}, "x") == "sparse-hybrid"
    assert _pipeline_name({"name": "my-pipe"}, "my-pipe-20260101-1") == "my-pipe"


def test_read_run_file_yields_a_row_per_claude_stage(tmp_path):
    f = _run(tmp_path, "pipe-20260909-1", "pipe-20260909-1", {
        "plan": ("sess-a", 0.5, 100, 2000),
        "implement": ("sess-b", 1.5, 200, 5000),
        "noclaude": (None, 0, 0, 0),   # a stage that never called claude
    })
    rows = read_run_file(f)
    assert len(rows) == 2                       # the None-session stage is skipped
    by = {r["stage"]: r for r in rows}
    assert by["implement"]["session_id"] == "sess-b"
    assert by["implement"]["pipeline"] == "pipe"
    assert by["implement"]["glitch_cost_usd"] == 1.5


def test_read_handles_a_stages_list_shape_too(tmp_path):
    d = tmp_path / ".glitch" / "runs"; d.mkdir(parents=True)
    (d / "r.json").write_text(json.dumps({
        "run_id": "r-20260101-1", "project_dir": str(tmp_path),
        "stages": [{"name": "build", "session_id": "s1", "cost_usd": 2.0}]}), encoding="utf-8")
    rows = read_run_file(d / "r.json")
    assert rows and rows[0]["stage"] == "build" and rows[0]["session_id"] == "s1"


def test_a_corrupt_run_file_is_skipped_not_fatal(tmp_path):
    d = tmp_path / ".glitch" / "runs"; d.mkdir(parents=True)
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    assert read_run_file(d / "bad.json") == []


def test_stage_rows_reads_extra_roots(tmp_path):
    _run(tmp_path, "p-20260101-1", "p-20260101-1", {"s": ("sid1", 1.0, 10, 20)})
    rows = stage_rows(tmp_path / "no-transcripts", extra_roots=[tmp_path])
    assert len(rows) == 1 and rows[0]["session_id"] == "sid1"


def test_pipeline_costs_joins_session_cost(tmp_path, monkeypatch):
    _run(tmp_path, "alpha-20260101-1", "alpha-20260101-1",
         {"plan": ("sid-A", 0.2, 10, 100), "impl": ("sid-B", 0.8, 20, 300)})
    import tokendog.glitch_runs as gr
    from tokendog.outcomes import SessionFacts
    from datetime import datetime, timezone
    T = datetime(2026, 1, 1, tzinfo=timezone.utc)
    facts = [SessionFacts(session="sid-A", cwd=str(tmp_path), project=tmp_path.name,
                          first=T, last=T, turns=3, files=set()),
             SessionFacts(session="sid-B", cwd=str(tmp_path), project=tmp_path.name,
                          first=T, last=T, turns=5, files=set())]
    monkeypatch.setattr(gr, "session_facts", lambda *a, **k: facts)
    import tokendog.report as rp
    monkeypatch.setattr(rp, "cost_summary", lambda **k: {"rows": [
        {"key": "sid-A", "est_cost_usd": 3.0, "input_tokens": 10, "cache_read_tokens": 1000,
         "cache_creation_tokens": 0, "output_tokens": 100},
        {"key": "sid-B", "est_cost_usd": 7.0, "input_tokens": 20, "cache_read_tokens": 2000,
         "cache_creation_tokens": 0, "output_tokens": 300}]})
    d = gr.pipeline_costs(tmp_path, extra_roots=[tmp_path])
    assert d["totals"]["pipelines"] == 1
    g = d["pipelines"][0]
    assert g["pipeline"] == "alpha" and g["sessions"] == 2 and g["stages"] == 2
    assert g["est_cost_usd"] == 10.0            # 3 + 7, session cost joined
    assert round(g["glitch_cost_usd"], 2) == 1.0  # 0.2 + 0.8
