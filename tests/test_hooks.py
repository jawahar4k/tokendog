"""The prepare-commit-msg hook and exact trailer-based attribution."""
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tokendog.hooks import HOOK_NAME, MARKER, format_install, hook_status, install, remove
from tokendog.outcomes import SESSION_TRAILER, git_commits


def _has_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_git(), reason="git not available")

ENV = None


def _repo(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "me@here"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Me"], check=True, env=env)
    return repo, env


def _commit(repo, env, msg, *, session=None):
    (repo / "f.ts").write_text(os.urandom(6).hex(), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    e = dict(env)
    if session is not None:
        e["CLAUDE_CODE_SESSION_ID"] = session
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True, env=e)


# --- install / status / remove ----------------------------------------


def test_install_creates_an_executable_hook(tmp_path):
    repo, _ = _repo(tmp_path)
    r = install(str(repo))
    assert r["ok"] and r["action"] == "installed"
    hook = repo / ".git" / "hooks" / HOOK_NAME
    assert hook.exists() and MARKER in hook.read_text()
    assert os.access(hook, os.X_OK)


def test_status_reports_installed(tmp_path):
    repo, _ = _repo(tmp_path)
    install(str(repo))
    st = hook_status(str(repo))
    assert st["installed"] and not st["foreign"]


def test_install_refuses_a_foreign_hook_without_force(tmp_path):
    repo, _ = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / HOOK_NAME
    hook.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    r = install(str(repo))
    assert not r["ok"] and "force" in r["reason"]
    assert "echo hi" in hook.read_text()      # untouched


def test_force_appends_to_a_foreign_hook_without_destroying_it(tmp_path):
    repo, _ = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / HOOK_NAME
    hook.write_text("#!/bin/sh\necho keepme\n", encoding="utf-8")
    r = install(str(repo), force=True)
    assert r["ok"] and r["action"] == "appended"
    text = hook.read_text()
    assert "echo keepme" in text and MARKER in text


def test_remove_deletes_our_own_hook(tmp_path):
    repo, _ = _repo(tmp_path)
    install(str(repo))
    r = remove(str(repo))
    assert r["ok"] and r["action"] == "removed"
    assert not (repo / ".git" / "hooks" / HOOK_NAME).exists()


def test_remove_leaves_a_foreign_hook_alone(tmp_path):
    repo, _ = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / HOOK_NAME
    hook.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    r = remove(str(repo))
    assert not r["ok"]
    assert hook.exists()


def test_install_on_a_non_repo_is_reported_not_crashed(tmp_path):
    r = install(str(tmp_path))
    assert not r["ok"] and "not a git" in r["reason"]


# --- the hook actually stamps commits (end to end) --------------------


def test_a_commit_from_a_session_gets_the_trailer(tmp_path):
    repo, env = _repo(tmp_path)
    install(str(repo))
    _commit(repo, env, "feat: a thing", session="sess-abc-123")
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    until = datetime.now(timezone.utc) + timedelta(minutes=5)
    commits = git_commits(str(repo), since, until)
    assert len(commits) == 1
    assert commits[0].session == "sess-abc-123"


def test_a_hand_commit_gets_no_trailer(tmp_path):
    """No CLAUDE_CODE_SESSION_ID in the env → the hook is inert, and the commit
    is correctly attributed to no session."""
    repo, env = _repo(tmp_path)
    install(str(repo))
    _commit(repo, env, "chore: by hand", session=None)
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    until = datetime.now(timezone.utc) + timedelta(minutes=5)
    commits = git_commits(str(repo), since, until)
    assert commits[0].session is None


def test_the_trailer_is_not_duplicated_on_a_second_stamp(tmp_path):
    """Committing twice in the same session must not accrete trailers."""
    repo, env = _repo(tmp_path)
    install(str(repo))
    _commit(repo, env, "one", session="s1")
    _commit(repo, env, "two", session="s1")
    log = subprocess.run(["git", "-C", str(repo), "log", "--format=%B"],
                         capture_output=True, text=True, env=env).stdout
    assert log.count(f"{SESSION_TRAILER}: s1") == 2   # one per commit, not doubled within one
