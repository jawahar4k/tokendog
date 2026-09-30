"""Capture: new bytes only, prefilter, generate, gate, critic, save — and retry on failure."""
import json

import pytest

from tokendog import learn_model
from tokendog.learn_capture import capture, offset_path

SESSION = "abcd1234-0000-4000-8000-abcdefabcdef"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "state"))
    return tmp_path


def _tool_use(tid, name, **inp):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _tool_result(tid, text, *, error=False):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}}


def _fix(i):
    return [_tool_use(f"e{i}", "Bash", command=f"curl upload {i}"),
            _tool_result(f"e{i}", f"Error: 403 Forbidden from staging, request body {i}.4MB too big",
                         error=True),
            # A real retry: it runs curl again, after splitting the body.
            _tool_use(f"s{i}", "Bash", command=f"split -b 900k body{i} && curl upload {i}"),
            _tool_result(f"s{i}", "uploaded")]


def _transcript(tmp_path, records):
    p = tmp_path / "t.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return p


def _repo(tmp_path):
    r = tmp_path / "repo"; r.mkdir(exist_ok=True)
    return r


LESSON = {"title": "Staging WAF rejects request bodies over 1MB",
          "rule": "Split uploads to staging into chunks under 1MB; the WAF returns a bare 403.",
          "why": "The 403 has no body, so it reads as an auth failure.",
          "evidence": "Error: 403 Forbidden from staging", "paths": [], "tags": ["staging"]}


def _model(monkeypatch, generator, critic_reply='[{"i": 0, "keep": true}]'):
    calls = []

    def run(prompt, **kw):
        calls.append("critic" if "Your default is REJECT" in prompt else "generate")
        if calls[-1] == "critic":
            return critic_reply
        return generator
    monkeypatch.setattr(learn_model, "run_model", run)
    return calls


def _status(repo):
    return json.loads((repo / ".claude" / "learnings" / "_local" / ".status.json").read_text())


def test_a_quiet_session_never_reaches_a_model(home, monkeypatch):
    calls = _model(monkeypatch, "[]")
    t = _transcript(home, _fix(1))                     # score 2, below 6
    out = capture(SESSION, t, _repo(home))
    assert out["state"] == "skipped" and calls == []
    assert _status(_repo(home))["state"] == "skipped"


def test_a_lesson_survives_generator_gates_and_critic(home, monkeypatch):
    calls = _model(monkeypatch, json.dumps([LESSON]))
    t = _transcript(home, _fix(1) + _fix(2) + _fix(3))
    out = capture(SESSION, t, _repo(home))
    assert out["state"] == "done" and out["added"] == 1
    assert calls == ["generate", "critic"]
    saved = list((_repo(home) / ".claude" / "learnings" / "_local").glob("*.md"))
    assert len(saved) == 1 and "Staging WAF" in saved[0].read_text()
    assert _status(_repo(home)) | {"at": 0} == {"state": "done", "added": 1, "at": 0}


def test_the_same_turns_are_never_mined_twice(home, monkeypatch):
    """PreCompact and SessionEnd both fire. The second must see only new bytes."""
    calls = _model(monkeypatch, json.dumps([LESSON]))
    t = _transcript(home, _fix(1) + _fix(2) + _fix(3))
    capture(SESSION, t, _repo(home))
    out = capture(SESSION, t, _repo(home))
    assert out["state"] == "skipped" and calls == ["generate", "critic"]


def test_a_failed_generator_leaves_the_turns_to_retry(home, monkeypatch):
    """Failed is not "nothing to learn". Advancing past it loses the session."""
    _model(monkeypatch, None)
    t = _transcript(home, _fix(1) + _fix(2) + _fix(3))
    assert capture(SESSION, t, _repo(home))["state"] == "failed"
    calls = _model(monkeypatch, json.dumps([LESSON]))
    assert capture(SESSION, t, _repo(home))["added"] == 1
    assert calls == ["generate", "critic"]


def test_a_failed_critic_keeps_nothing_and_retries(home, monkeypatch):
    _model(monkeypatch, json.dumps([LESSON]), critic_reply=None)
    t = _transcript(home, _fix(1) + _fix(2) + _fix(3))
    out = capture(SESSION, t, _repo(home))
    assert out["state"] == "failed"
    assert not list((_repo(home) / ".claude" / "learnings" / "_local").glob("*.md"))


def test_the_critic_saying_no_is_an_answer(home, monkeypatch):
    _model(monkeypatch, json.dumps([LESSON]), critic_reply='[{"i": 0, "keep": false}]')
    t = _transcript(home, _fix(1) + _fix(2) + _fix(3))
    out = capture(SESSION, t, _repo(home))
    assert out["state"] == "done" and out["added"] == 0


def test_seeing_your_own_lesson_again_counts_it(home, monkeypatch):
    _model(monkeypatch, json.dumps([LESSON]))
    capture(SESSION, _transcript(home, _fix(1) + _fix(2) + _fix(3)), _repo(home))
    other = "zzzz1234-0000-4000-8000-abcdefabcdef"
    t2 = home / "t2.jsonl"
    t2.write_text("\n".join(json.dumps(r) for r in _fix(4) + _fix(5) + _fix(6)) + "\n")
    out = capture(other, t2, _repo(home))
    assert out["corroborated"] == 1
    text = next((_repo(home) / ".claude" / "learnings" / "_local").glob("*.md")).read_text()
    assert "sessions: 2" in text


def test_learning_can_be_switched_off(home, monkeypatch):
    calls = _model(monkeypatch, json.dumps([LESSON]))
    monkeypatch.setenv("TOKENDOG_LEARN", "off")
    out = capture(SESSION, _transcript(home, _fix(1) + _fix(2) + _fix(3)), _repo(home))
    assert out["state"] == "off" and calls == []


def test_a_missing_transcript_is_not_a_crash(home):
    assert capture(SESSION, home / "nope.jsonl", _repo(home))["state"] in ("skipped", "failed")


def test_a_bad_session_id_is_refused(home):
    assert capture("../x", home / "t.jsonl", _repo(home))["state"] == "refused"


def test_only_one_worker_runs_per_session(home, monkeypatch):
    _model(monkeypatch, "[]")
    lock = offset_path(SESSION).with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("99999")
    t = _transcript(home, _fix(1) + _fix(2) + _fix(3))
    assert capture(SESSION, t, _repo(home))["state"] == "locked"


def test_a_stale_lock_does_not_block_forever(home, monkeypatch):
    import os, time
    _model(monkeypatch, "[]")
    lock = offset_path(SESSION).with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("99999")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    t = _transcript(home, _fix(1) + _fix(2) + _fix(3))
    assert capture(SESSION, t, _repo(home))["state"] != "locked"
