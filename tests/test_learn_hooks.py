"""The learnings hooks and CLI: fast, recursion-proof, and honest about failure."""
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tokendog.learn import forget, save_lesson

SCRIPTS = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts"
SESSION = "abcd1234-0000-4000-8000-abcdefabcdef"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, payload, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = mod.main()
    return rc, capsys.readouterr().out


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "state"))
    r = tmp_path / "repo"
    (r / ".git").mkdir(parents=True)
    (r / "sub").mkdir()
    return r


LESSON = {"title": "Staging WAF rejects request bodies over 1MB",
          "rule": "Split uploads to staging into chunks under 1MB; the WAF returns a bare 403.",
          "why": "w", "evidence": "Error: 403 Forbidden", "paths": [], "tags": []}


# --- apply -------------------------------------------------------------------


def test_apply_injects_lessons_as_additional_context(repo, monkeypatch, capsys):
    save_lesson(repo, LESSON, session_hash="a")
    rc, out = _run(_load("learn_apply"), {"cwd": str(repo / "sub"), "source": "startup"},
                   monkeypatch, capsys)
    assert rc == 0
    ctx = json.loads(out)["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart"
    assert "Staging WAF" in ctx["additionalContext"]


def test_apply_finds_the_repo_from_a_subdirectory(repo, monkeypatch, capsys):
    save_lesson(repo, LESSON, session_hash="a")
    _, out = _run(_load("learn_apply"), {"cwd": str(repo / "sub")}, monkeypatch, capsys)
    assert "Staging WAF" in out


def test_apply_says_nothing_when_there_is_nothing(repo, monkeypatch, capsys):
    rc, out = _run(_load("learn_apply"), {"cwd": str(repo)}, monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""


def test_apply_outside_a_repo_does_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "state"))
    rc, out = _run(_load("learn_apply"), {"cwd": str(tmp_path)}, monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""


# --- recursion ---------------------------------------------------------------


@pytest.mark.parametrize("hook", ["learn_apply", "learn_capture"])
def test_a_learning_worker_session_never_triggers_learning(repo, hook, monkeypatch, capsys):
    """The model call is itself a Claude Code session. If its SessionEnd mined
    it, that would spawn another model call, whose SessionEnd would mine it —
    a loop that spends money until something breaks."""
    save_lesson(repo, LESSON, session_hash="a")
    monkeypatch.setenv("TOKENDOG_LEARN_CHILD", "1")
    spawned = []
    import tokendog.learn_worker as w
    monkeypatch.setattr(w, "spawn", lambda *a: spawned.append(a) or True)
    rc, out = _run(_load(hook), {"cwd": str(repo), "session_id": SESSION,
                                 "transcript_path": str(repo / "t.jsonl")}, monkeypatch, capsys)
    assert rc == 0 and out.strip() == "" and spawned == []


# --- capture -----------------------------------------------------------------


def test_capture_hands_the_work_to_a_detached_worker(repo, monkeypatch, capsys):
    """The hook must return in ~100 ms; the model calls take seconds."""
    spawned = []
    import tokendog.learn_worker as w
    monkeypatch.setattr(w, "spawn", lambda *a: spawned.append(a) or True)
    rc, out = _run(_load("learn_capture"), {"cwd": str(repo / "sub"), "session_id": SESSION,
                                            "transcript_path": "/x/t.jsonl"}, monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""
    assert spawned == [("mine", SESSION, "/x/t.jsonl", str(repo.resolve()))]


def test_capture_can_be_switched_off(repo, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_LEARN", "off")
    spawned = []
    import tokendog.learn_worker as w
    monkeypatch.setattr(w, "spawn", lambda *a: spawned.append(a) or True)
    _run(_load("learn_capture"), {"cwd": str(repo), "session_id": SESSION,
                                  "transcript_path": "/x/t.jsonl"}, monkeypatch, capsys)
    assert spawned == []


@pytest.mark.parametrize("hook", ["learn_apply", "learn_capture"])
def test_hooks_never_crash_the_session(hook, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert _load(hook).main() == 0


def test_the_hooks_are_wired_to_the_right_events():
    hooks = json.loads((SCRIPTS.parent / "hooks" / "hooks.json").read_text())["hooks"]
    blob = {ev: json.dumps(v) for ev, v in hooks.items()}
    assert "learn_capture.py" in blob["PreCompact"] and "learn_capture.py" in blob["SessionEnd"]
    assert "learn_apply.py" in blob["SessionStart"]
    assert "learn_capture.py" not in blob["SessionStart"]


# --- forget ------------------------------------------------------------------


def test_forget_removes_a_lesson_and_remembers_it(repo):
    p = save_lesson(repo, LESSON, session_hash="a")
    assert forget(repo, p.name)["title"] == LESSON["title"]
    assert not p.exists()
    forgotten = (repo / ".claude" / "learnings" / "_local" / ".forgotten").read_text()
    assert LESSON["title"] in forgotten


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", ".status.json",
                                 ".gitignore", "a/b.md", "nope.md"])
def test_forget_only_touches_your_own_lesson_files(repo, bad):
    save_lesson(repo, LESSON, session_hash="a")
    assert forget(repo, bad) is None


# --- the command -------------------------------------------------------------


def test_the_learnings_command_lists_lessons(repo, monkeypatch):
    save_lesson(repo, LESSON, session_hash="a")
    out = subprocess.run([sys.executable, "-m", "tokendog.report", "learnings"], cwd=repo,
                         capture_output=True, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")})
    assert out.returncode == 0, out.stderr
    assert "Staging WAF" in out.stdout and "automatic sharing: off" in out.stdout
