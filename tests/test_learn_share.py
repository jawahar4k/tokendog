"""Sharing, against a real local git remote. Only GitHub is faked."""
import json
import subprocess
import time

import pytest

from tokendog import learn_share
from tokendog.learn import save_lesson
from tokendog.learn_share import share, share_due


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "state"))
    origin = tmp_path / "origin.git"
    git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    git("clone", str(origin), str(work), cwd=tmp_path)
    git("config", "user.email", "dev@example.com", cwd=work)
    git("config", "user.name", "Dev", cwd=work)
    (work / "README.md").write_text("hi\n")
    (work / ".gitignore").write_text(".claude/\n")          # the case -f exists for
    git("add", "-A", cwd=work)
    git("commit", "-m", "init", cwd=work)
    git("push", "origin", "main", cwd=work)
    return work


@pytest.fixture
def github(monkeypatch):
    calls = []
    state = {"prs": []}

    def fake(args, cwd):
        calls.append(args)
        if args[:2] == ["auth", "status"]:
            return "ok"
        if args[:2] == ["pr", "list"]:
            return json.dumps(state["prs"])
        if args[:2] == ["pr", "create"]:
            head = args[args.index("--head") + 1]
            state["prs"].append({"number": 7, "headRefName": head, "url": "https://gh/pr/7"})
            return "https://gh/pr/7"
        if args[:2] == ["pr", "comment"]:
            return ""
        raise AssertionError(args)
    monkeypatch.setattr(learn_share, "gh", fake)
    return calls, state


LESSON = {"title": "Staging WAF rejects request bodies over 1MB",
          "rule": "Split uploads to staging into chunks under 1MB; the WAF returns a bare 403.",
          "why": "The 403 has no body.", "evidence": "Error: 403 Forbidden", "paths": [],
          "tags": ["staging"]}


def _aged(repo, lesson=LESSON, hours=48):
    p = save_lesson(repo, lesson, session_hash="abc")
    old = int((time.time() - hours * 3600) * 1000)
    p.write_text(p.read_text().replace("created_at: ", f"created_at: {old}\nx_old: ", 1))
    return p


def test_sharing_is_off_unless_switched_on(repo, github, monkeypatch):
    """It opens a PR on someone's repository. That is not a default."""
    monkeypatch.delenv("TOKENDOG_LEARN_SHARE", raising=False)
    _aged(repo)
    assert share(repo)["state"] == "off"
    assert github[0] == []


def test_a_lesson_younger_than_a_day_waits(repo, github, monkeypatch):
    """The day is the author's chance to forget a bad one first."""
    monkeypatch.setenv("TOKENDOG_LEARN_SHARE", "on")
    save_lesson(repo, LESSON, session_hash="abc")
    assert share(repo)["state"] == "nothing"
    assert not share_due(repo)


def test_a_share_opens_one_pr_and_never_touches_the_checkout(repo, github, monkeypatch):
    monkeypatch.setenv("TOKENDOG_LEARN_SHARE", "on")
    local = _aged(repo)
    (repo / "wip.txt").write_text("uncommitted work")
    before = git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
    out = share(repo)
    assert out["state"] == "done", out
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == before
    assert (repo / "wip.txt").read_text() == "uncommitted work"
    assert git("worktree", "list", cwd=repo).count("\n") == 0      # throwaway worktree removed

    files = git("ls-tree", "-r", "--name-only", f"origin/{out['branch']}", cwd=repo) \
        if not git("fetch", "origin", cwd=repo) else ""
    files = git("ls-tree", "-r", "--name-only", f"origin/{out['branch']}", cwd=repo)
    assert ".claude/learnings/INDEX.md" in files
    assert any(f.startswith(".claude/learnings/") and f.endswith(".md") and "INDEX" not in f
               for f in files.splitlines())
    assert "_local" not in files                                   # personal never pushed
    assert 'shared: "https://gh/pr/7"' in local.read_text()


def test_personal_fields_stay_behind(repo, github, monkeypatch):
    monkeypatch.setenv("TOKENDOG_LEARN_SHARE", "on")
    _aged(repo)
    out = share(repo)
    git("fetch", "origin", cwd=repo)
    name = [f for f in git("ls-tree", "-r", "--name-only", f"origin/{out['branch']}", cwd=repo)
            .splitlines() if f.endswith(".md") and "INDEX" not in f and "README" not in f][0]
    text = git("show", f"origin/{out['branch']}:{name}", cwd=repo)
    for field in ("seen_in", "created_at", "shared:", "sessions"):
        assert field not in text, field
    assert "developers: 1" in text


def test_an_open_learnings_pr_is_appended_to_not_duplicated(repo, github, monkeypatch):
    monkeypatch.setenv("TOKENDOG_LEARN_SHARE", "on")
    _aged(repo)
    first = share(repo)
    _aged(repo, LESSON | {"title": "pnpm workspaces need a root lockfile",
                          "rule": "Run pnpm install at the root, never inside a package."})
    second = share(repo)
    assert second["branch"] == first["branch"]
    creates = [c for c in github[0] if c[:2] == ["pr", "create"]]
    assert len(creates) == 1


def test_a_lesson_someone_already_proposed_is_corroborated(repo, github, monkeypatch):
    monkeypatch.setenv("TOKENDOG_LEARN_SHARE", "on")
    _aged(repo)
    first = share(repo)
    _aged(repo, LESSON | {"title": "Staging WAF rejects request bodies over 1 MB"})
    out = share(repo)
    assert out["corroborated"] == 1
    git("fetch", "origin", cwd=repo)
    index = git("show", f"origin/{first['branch']}:.claude/learnings/INDEX.md", cwd=repo)
    assert "corroborated by 2 developers" in index


def test_no_gh_login_keeps_the_lessons_for_next_time(repo, monkeypatch):
    monkeypatch.setenv("TOKENDOG_LEARN_SHARE", "on")
    local = _aged(repo)

    def no_auth(args, cwd):
        raise RuntimeError("not logged in")
    monkeypatch.setattr(learn_share, "gh", no_auth)
    assert share(repo)["state"] == "failed"
    assert 'shared: "no"' in local.read_text()


def test_a_repo_can_opt_out_for_everyone(repo, github, monkeypatch):
    monkeypatch.setenv("TOKENDOG_LEARN_SHARE", "on")
    (repo / ".claude" / "learnings").mkdir(parents=True, exist_ok=True)
    (repo / ".claude" / "learnings" / "config.json").write_text('{"share": false}')
    _aged(repo)
    assert share(repo)["state"] == "off"


def test_the_commit_prefix_is_used(repo, github, monkeypatch):
    monkeypatch.setenv("TOKENDOG_LEARN_SHARE", "on")
    (repo / ".claude" / "learnings").mkdir(parents=True, exist_ok=True)
    (repo / ".claude" / "learnings" / "config.json").write_text('{"commitPrefix": "PROJ-12: "}')
    _aged(repo)
    out = share(repo)
    git("fetch", "origin", cwd=repo)
    assert git("log", "-1", "--format=%s", f"origin/{out['branch']}", cwd=repo).startswith("PROJ-12: ")
