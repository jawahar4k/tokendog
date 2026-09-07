"""Cost must be attributable to a project.

`~/.claude/projects` holds every project on the machine, so an unscoped roll-up
answers a question nobody asked: "what did this laptop spend." The transcripts
already record `cwd` on every turn, so the attribution was being read and
thrown away — and a real report had to carry a hand-written caveat saying its
numbers covered more than the project in question.
"""
import json

from tokendog import report
from tokendog.backend import LocalSQLiteBackend, QueryFilter
from tokendog.event import TokenEvent, RUNTIME_CLAUDE, SOURCE_TRANSCRIPT
from tokendog.transcripts import (event_from_record, known_projects,
                                  project_of, read_transcripts)


def _turn(cwd, *, out=10, ts="2026-09-07T00:00:00+00:00"):
    rec = {"timestamp": ts, "sessionId": "s", "type": "assistant",
           "message": {"model": "sonnet", "usage": {"input_tokens": 0,
                                                    "output_tokens": out,
                                                    "cache_read_input_tokens": 0}}}
    if cwd is not None:
        rec["cwd"] = cwd
    return rec


def test_project_is_the_cwd_basename():
    assert project_of({"cwd": "/Users/someone/projects/contextflow"}) == "contextflow"
    assert project_of({"cwd": "/Users/someone/projects/contextflow/"}) == "contextflow"


def test_project_is_a_basename_not_a_path():
    """A full path leaks the home directory, and often a client name with it."""
    got = project_of({"cwd": "/Users/realname/clients/acme-corp/backend"})
    assert got == "backend"
    assert "realname" not in got and "acme" not in got


def test_missing_cwd_is_unknown_not_guessed():
    """The directory name encodes the path with `/` replaced by `-`, so a
    project called `my-app` cannot be told apart from a directory `my/app`.
    Guessing would put spend in the wrong bucket silently."""
    for record in ({}, {"cwd": ""}, {"cwd": "   "}, {"cwd": None}):
        assert project_of(record) is None


def test_event_carries_the_project():
    e = event_from_record(_turn("/x/y/contextflow"))
    assert e.project == "contextflow"


def test_rollup_can_group_and_filter_by_project():
    backend = LocalSQLiteBackend(path=":memory:")
    for cwd, out in (("/a/alpha", 1_000_000), ("/a/beta", 2_000_000)):
        backend.ingest(event_from_record(_turn(cwd, out=out)))
    grouped = {r.key: r for r in backend.query(QueryFilter(group_by="project")).rows}
    assert set(grouped) == {"alpha", "beta"}
    assert grouped["beta"].est_cost_usd > grouped["alpha"].est_cost_usd

    only = backend.query(QueryFilter(group_by="project", project="alpha")).rows
    assert [r.key for r in only] == ["alpha"]


def test_unattributed_turns_are_excluded_not_lumped_in():
    backend = LocalSQLiteBackend(path=":memory:")
    backend.ingest(event_from_record(_turn("/a/alpha")))
    backend.ingest(event_from_record(_turn(None)))
    rows = backend.query(QueryFilter(group_by="project", project="alpha")).rows
    assert [r.key for r in rows] == ["alpha"] and rows[0].calls == 1


def test_reports_are_scoped_and_labelled(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "home"))
    root = tmp_path / "projects" / "-a-alpha"
    root.mkdir(parents=True)
    (root / "sess.jsonl").write_text(
        "\n".join(json.dumps(_turn(c)) for c in ("/a/alpha", "/a/alpha")))
    (tmp_path / "projects" / "-a-beta").mkdir()
    (tmp_path / "projects" / "-a-beta" / "s2.jsonl").write_text(json.dumps(_turn("/a/beta")))
    monkeypatch.setenv("TOKENDOG_TRANSCRIPT_ROOT", str(tmp_path / "projects"))

    assert known_projects() == ["alpha", "beta"]
    assert sum(1 for _ in read_transcripts()) == 3

    summary = report.cost_summary(group_by="project", project="alpha")
    assert [r["key"] for r in summary["rows"]] == ["alpha"]
    assert summary["rows"][0]["calls"] == 2
    # the reader must be able to see the report was scoped
    assert "alpha" in report.format_rollup(summary)

    bands = report.band_report_data(project="alpha")
    assert bands["turns"] == 2
    assert "alpha" in report.format_bands(bands)


def test_dollars_are_labelled_as_attribution_not_a_bill():
    """A subscription has no per-token charge, and the estimate is not a
    rate-limit proxy either: it applies price weights quota accounting does
    not. A report that just says "$" invites both misreadings."""
    out = report.format_rollup({"group_by": "runtime", "rows": []})
    low = out.lower()
    assert "not an invoice" in low
    assert "subscription" in low
    assert "rate-limit proxy" in low
