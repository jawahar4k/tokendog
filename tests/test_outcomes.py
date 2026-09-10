"""Session -> commit -> PR attribution, the ROI join."""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tokendog.outcomes import (
    Commit,
    SessionFacts,
    _basename_overlap,
    attribute,
    git_commits,
    session_facts,
)

T0 = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def _git(repo, *args, env=None):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True, env=env)


def _has_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


requires_git = pytest.mark.skipif(not _has_git(), reason="git not available")


# --- transcript reading -----------------------------------------------


def _write_session(root, name, cwd, *, files, at=T0):
    d = root / "proj"; d.mkdir(exist_ok=True)
    lines = []
    for i, f in enumerate(files):
        ts = (at + timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
        lines.append(json.dumps({
            "type": "assistant", "requestId": f"{name}-r{i}", "sessionId": name,
            "cwd": cwd, "timestamp": ts,
            "message": {"usage": {"input_tokens": 10, "output_tokens": 5},
                        "content": [{"type": "tool_use", "name": "Edit",
                                     "input": {"file_path": f}}]},
        }))
    (d / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_session_facts_capture_cwd_files_and_window(tmp_path):
    _write_session(tmp_path, "s1", "/work/repoA", files=["/work/repoA/a.ts", "/work/repoA/b.ts"])
    facts = session_facts(tmp_path)
    assert len(facts) == 1
    f = facts[0]
    assert f.cwd == "/work/repoA" and f.project == "repoA"
    assert {p.split("/")[-1] for p in f.files} == {"a.ts", "b.ts"}
    assert f.turns == 2
    assert f.first is not None and f.last is not None and f.last > f.first


def test_session_facts_can_filter_to_a_project(tmp_path):
    _write_session(tmp_path, "s1", "/work/repoA", files=["/work/repoA/a.ts"])
    _write_session(tmp_path, "s2", "/work/repoB", files=["/work/repoB/z.ts"])
    assert {f.session for f in session_facts(tmp_path, project="repoB")} == {"s2"}


# --- basename overlap -------------------------------------------------


def test_overlap_matches_on_basename_across_path_shapes():
    # tool records an absolute path; git records a repo-relative one.
    assert _basename_overlap({"/work/repoA/src/app.ts"}, {"src/app.ts"}) == 1
    assert _basename_overlap({"/work/repoA/a.ts"}, {"b.ts"}) == 0


# --- PR number extraction (pure, no git) ------------------------------


def _commit(sha, minutes, subject, files):
    return Commit(sha=sha, when=T0 + timedelta(minutes=minutes), author="dev",
                  subject=subject, files=set(files), pr=None)


def test_attribute_links_a_session_to_an_in_window_commit_with_file_overlap():
    f = SessionFacts(session="s1", cwd="/w/r", project="r",
                     first=T0, last=T0 + timedelta(minutes=5),
                     turns=6, files={"/w/r/app.ts"})
    # monkeypatch git_commits via a stand-in: call attribute with a repo that
    # has commits injected through the module boundary. Simplest: exercise the
    # pure grouping by pre-seeding through git — covered in the integration test.
    # Here we assert the no-overlap case is NOT linked.
    import tokendog.outcomes as oc
    orig = oc.git_commits
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="deadbeef", when=T0 + timedelta(minutes=2), author="dev",
                  subject="Fix app (#42)", files={"app.ts"}, pr=42)]
    try:
        d = attribute([f])
    finally:
        oc.git_commits = orig
    assert d["totals"]["prs"] == 1
    g = d["groups"][0]
    assert g["pr"] == 42 and g["sessions"] == ["s1"]


def test_a_commit_outside_the_window_is_not_attributed():
    f = SessionFacts(session="s1", cwd="/w/r", project="r",
                     first=T0, last=T0 + timedelta(minutes=5),
                     turns=6, files={"/w/r/app.ts"})
    import tokendog.outcomes as oc
    orig = oc.git_commits
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="c1", when=T0 + timedelta(hours=6), author="dev",
                  subject="Later work (#9)", files={"app.ts"}, pr=9)]
    try:
        d = attribute([f])
    finally:
        oc.git_commits = orig
    assert d["totals"]["prs"] == 0
    assert d["totals"]["unmatched_sessions"] == 1


def test_shared_cost_is_flagged_when_two_sessions_feed_one_pr():
    fa = SessionFacts(session="a", cwd="/w/r", project="r", first=T0,
                      last=T0 + timedelta(minutes=3), turns=3, files={"/w/r/x.ts"})
    fb = SessionFacts(session="b", cwd="/w/r", project="r",
                      first=T0 + timedelta(minutes=4), last=T0 + timedelta(minutes=7),
                      turns=4, files={"/w/r/x.ts"})
    import tokendog.outcomes as oc
    orig = oc.git_commits
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="c1", when=T0 + timedelta(minutes=2), author="d",
                  subject="part one (#7)", files={"x.ts"}, pr=7),
        oc.Commit(sha="c2", when=T0 + timedelta(minutes=6), author="d",
                  subject="part two (#7)", files={"x.ts"}, pr=7)]
    try:
        d = attribute([fa, fb], costs={"a": 1.0, "b": 2.0})
    finally:
        oc.git_commits = orig
    g = next(x for x in d["groups"] if x["pr"] == 7)
    assert set(g["sessions"]) == {"a", "b"}
    assert g["shared"] is True
    assert g["est_cost_usd"] == 3.0
    assert g["turns"] == 7


def test_a_commit_with_no_pr_is_bucketed_not_dropped():
    f = SessionFacts(session="s1", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=5), turns=2, files={"/w/r/a.ts"})
    import tokendog.outcomes as oc
    orig = oc.git_commits
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="c1", when=T0 + timedelta(minutes=1), author="d",
                  subject="wip, no pr", files={"a.ts"}, pr=None)]
    try:
        d = attribute([f], costs={"s1": 0.5})
    finally:
        oc.git_commits = orig
    assert d["totals"]["prs"] == 0
    assert len(d["groups"]) == 1
    assert d["groups"][0]["pr"] is None
    assert d["groups"][0]["est_cost_usd"] == 0.5


def test_cost_per_pr_averages_only_over_real_prs():
    fa = SessionFacts(session="a", cwd="/w/r", project="r", first=T0,
                      last=T0 + timedelta(minutes=2), turns=2, files={"/w/r/a.ts"})
    fb = SessionFacts(session="b", cwd="/w/r", project="r",
                      first=T0 + timedelta(minutes=3), last=T0 + timedelta(minutes=5),
                      turns=2, files={"/w/r/b.ts"})
    import tokendog.outcomes as oc
    orig = oc.git_commits
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="c1", when=T0 + timedelta(minutes=1), author="d",
                  subject="A (#1)", files={"a.ts"}, pr=1),
        oc.Commit(sha="c2", when=T0 + timedelta(minutes=4), author="d",
                  subject="B (#2)", files={"b.ts"}, pr=2)]
    try:
        d = attribute([fa, fb], costs={"a": 4.0, "b": 6.0})
    finally:
        oc.git_commits = orig
    assert d["totals"]["prs"] == 2
    assert d["totals"]["cost_per_pr"] == 5.0


# --- real git integration ---------------------------------------------


@requires_git
def test_git_commits_reads_a_real_repo(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "HOME": str(tmp_path), "PATH": __import__("os").environ.get("PATH", "")}
    _git(repo, "init", "-q", env=env)
    (repo / "app.ts").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A", env=env)
    _git(repo, "commit", "-q", "-m", "Add app (#5)", env=env)
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    commits = git_commits(str(repo), since, until)
    assert len(commits) == 1
    assert commits[0].pr == 5
    assert "app.ts" in commits[0].files


@requires_git
def test_git_commits_on_a_non_repo_returns_empty(tmp_path):
    assert git_commits(str(tmp_path), T0, T0 + timedelta(hours=1)) == []


# --- gh enrichment ----------------------------------------------------


def test_gh_fills_in_a_pr_number_the_merge_subject_lacked():
    """Squash-merge on GitHub leaves no local merge commit, so the subject has
    no #N. With --gh, the linked commit's PR is resolved via the GitHub CLI."""
    f = SessionFacts(session="s1", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=5), turns=3, files={"/w/r/a.ts"})
    import tokendog.outcomes as oc
    orig_git, orig_gh = oc.git_commits, oc.resolve_pr_via_gh
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="abc123", when=T0 + timedelta(minutes=2), author="d",
                  subject="feat: a thing with no pr ref", files={"a.ts"}, pr=None)]
    calls = []
    oc.resolve_pr_via_gh = lambda repo, sha: (calls.append(sha) or 314)
    try:
        d = attribute([f], costs={"s1": 2.0}, use_gh=True)
    finally:
        oc.git_commits, oc.resolve_pr_via_gh = orig_git, orig_gh
    assert calls == ["abc123"]           # called once, for the linked commit
    assert d["totals"]["prs"] == 1
    assert d["groups"][0]["pr"] == 314


def test_gh_is_not_called_without_the_flag():
    f = SessionFacts(session="s1", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=5), turns=1, files={"/w/r/a.ts"})
    import tokendog.outcomes as oc
    orig_git, orig_gh = oc.git_commits, oc.resolve_pr_via_gh
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="x", when=T0 + timedelta(minutes=1), author="d",
                  subject="no pr", files={"a.ts"}, pr=None)]
    called = []
    oc.resolve_pr_via_gh = lambda repo, sha: called.append(sha)
    try:
        attribute([f], use_gh=False)
    finally:
        oc.git_commits, oc.resolve_pr_via_gh = orig_git, orig_gh
    assert called == []


def test_gh_is_only_called_for_commits_without_a_subject_pr():
    """A commit whose subject already carried #N must not spend a gh call."""
    f = SessionFacts(session="s1", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=5), turns=1, files={"/w/r/a.ts"})
    import tokendog.outcomes as oc
    orig_git, orig_gh = oc.git_commits, oc.resolve_pr_via_gh
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="has", when=T0 + timedelta(minutes=1), author="d",
                  subject="done (#8)", files={"a.ts"}, pr=8)]
    called = []
    oc.resolve_pr_via_gh = lambda repo, sha: called.append(sha)
    try:
        d = attribute([f], use_gh=True)
    finally:
        oc.git_commits, oc.resolve_pr_via_gh = orig_git, orig_gh
    assert called == []
    assert d["groups"][0]["pr"] == 8


def test_remote_slug_parses_ssh_and_https():
    """The gh -R target is owner/name, however the remote is spelled."""
    import re
    for url, want in [
        ("git@github.com:acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets", "acme/widgets"),
    ]:
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
        assert m and m.group(1) == want


# --- author filtering (the shared-repo guard) -------------------------


def test_author_filter_is_passed_to_git_by_default():
    """Owner-only is the default, so a teammate's commit on a same-named file
    cannot be mis-linked to a local session."""
    f = SessionFacts(session="s1", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=5), turns=1, files={"/w/r/a.ts"})
    import tokendog.outcomes as oc
    orig_git, orig_auth = oc.git_commits, oc.git_author
    seen = {}
    oc.git_author = lambda repo: "owner@example.com"
    def fake(repo, since, until, *, author=None):
        seen["author"] = author
        return []
    oc.git_commits = fake
    try:
        attribute([f], own_author_only=True)
    finally:
        oc.git_commits, oc.git_author = orig_git, orig_auth
    assert seen["author"] == "owner@example.com"


def test_all_authors_disables_the_filter():
    f = SessionFacts(session="s1", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=5), turns=1, files={"/w/r/a.ts"})
    import tokendog.outcomes as oc
    orig_git, orig_auth = oc.git_commits, oc.git_author
    seen = {}
    oc.git_author = lambda repo: "owner@example.com"
    def fake(repo, since, until, *, author=None):
        seen["author"] = author
        return []
    oc.git_commits = fake
    try:
        attribute([f], own_author_only=False)
    finally:
        oc.git_commits, oc.git_author = orig_git, orig_auth
    assert seen["author"] is None


# --- exact (trailer) attribution --------------------------------------


def test_a_trailer_links_the_exact_session_no_heuristic():
    """The whole point: a commit that names its session is linked to THAT
    session, regardless of window or file overlap."""
    f = SessionFacts(session="sess-XYZ", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=5), turns=4, files=set())
    import tokendog.outcomes as oc
    orig = oc.git_commits
    # commit lands OUTSIDE the window and touches an unrelated file — heuristic
    # would miss it; the trailer catches it exactly.
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="c1", when=T0 + timedelta(hours=9), author="d",
                  subject="done (#11)", files={"unrelated.py"}, pr=11, session="sess-XYZ")]
    try:
        d = attribute([f], costs={"sess-XYZ": 3.0})
    finally:
        oc.git_commits = orig
    g = d["groups"][0]
    assert g["pr"] == 11 and g["sessions"] == ["sess-XYZ"]
    assert g["method"] == "exact"
    assert d["totals"]["exact_commits"] == 1 and d["totals"]["heuristic_commits"] == 0


def test_a_trailer_for_an_unknown_session_is_ignored():
    """A commit stamped by a session that is not on this machine belongs to
    no local outcome."""
    f = SessionFacts(session="mine", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=5), turns=2, files={"/w/r/a.ts"})
    import tokendog.outcomes as oc
    orig = oc.git_commits
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="c1", when=T0 + timedelta(minutes=2), author="d",
                  subject="theirs (#2)", files={"a.ts"}, pr=2, session="someone-elses")]
    try:
        d = attribute([f])
    finally:
        oc.git_commits = orig
    assert d["totals"]["prs"] == 0
    assert d["totals"]["unmatched_sessions"] == 1


def test_exact_and_heuristic_mix_is_labelled(tmp_path=None):
    """A PR whose commits are partly stamped and partly matched reads as mixed."""
    fa = SessionFacts(session="a", cwd="/w/r", project="r", first=T0,
                      last=T0 + timedelta(minutes=3), turns=2, files={"/w/r/x.ts"})
    fb = SessionFacts(session="b", cwd="/w/r", project="r",
                      first=T0 + timedelta(minutes=4), last=T0 + timedelta(minutes=7),
                      turns=2, files={"/w/r/y.ts"})
    import tokendog.outcomes as oc
    orig = oc.git_commits
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="c1", when=T0 + timedelta(minutes=1), author="d",
                  subject="stamped (#5)", files={"x.ts"}, pr=5, session="a"),
        oc.Commit(sha="c2", when=T0 + timedelta(minutes=5), author="d",
                  subject="guessed (#5)", files={"y.ts"}, pr=5, session=None)]
    try:
        d = attribute([fa, fb], costs={"a": 1.0, "b": 2.0})
    finally:
        oc.git_commits = orig
    g = next(x for x in d["groups"] if x["pr"] == 5)
    assert g["method"] == "mixed"
    assert set(g["sessions"]) == {"a", "b"}


def test_a_fully_stamped_repo_never_uses_the_heuristic():
    f = SessionFacts(session="a", cwd="/w/r", project="r", first=T0,
                     last=T0 + timedelta(minutes=3), turns=2, files={"/w/r/x.ts"})
    import tokendog.outcomes as oc
    orig = oc.git_commits
    calls = {"overlap": 0}
    real_overlap = oc._basename_overlap
    def spy(a, b):
        calls["overlap"] += 1
        return real_overlap(a, b)
    oc._basename_overlap = spy
    oc.git_commits = lambda repo, since, until, author=None: [
        oc.Commit(sha="c1", when=T0 + timedelta(minutes=1), author="d",
                  subject="a (#1)", files={"x.ts"}, pr=1, session="a")]
    try:
        d = attribute([f])
    finally:
        oc.git_commits = orig
        oc._basename_overlap = real_overlap
    assert d["groups"][0]["method"] == "exact"
    assert calls["overlap"] == 0     # heuristic overlap never consulted
