"""The hook interpreter must be resolved, and its absence must be visible.

Hooks are launched as `python3 <script>`, and `python3` is frequently not the
environment tokendog was installed into. Every hook swallows the resulting
ImportError and exits 0, so the failure has no symptom: the plugin looks
installed and reports $0.00 forever. These tests pin both halves of the fix —
the re-exec that avoids the failure, and the one message that reports it when
the re-exec cannot find anywhere to go.
"""
import importlib.util
import json
import subprocess
import sys
import venv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "tokendog-plugin" / "scripts"
HOOKS = ("token_count.py", "budget_enforce.py", "truncate_output.py",
         "session_summary.py", "budget_alert.py", "sink_health.py")


def _bootstrap():
    spec = importlib.util.spec_from_file_location("tokendog_bootstrap",
                                                  SCRIPTS / "_bootstrap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bare_python(tmp_path_factory):
    """An interpreter that genuinely cannot import tokendog.

    Built rather than borrowed: `/usr/bin/python3` happens to lack tokendog on
    this machine but may have it on the next one, and a test that only passes
    by luck of the environment is not testing anything.

    Built from `sys.base_prefix`, NOT from `sys.executable`. `venv.create()`
    run from inside a venv copies the binary, so the result shares no symlink
    target with the venv we re-exec into — and the realpath-vs-abspath bug in
    the guard then goes undetected, because the two paths no longer collide.
    Building from the base makes this venv symlink to the same interpreter the
    dev venv does, which is the real-world shape: a project venv and a system
    python3 that resolve to one binary.
    """
    root = tmp_path_factory.mktemp("bare")
    base = Path(sys.base_prefix) / "bin" / "python3"
    if not base.is_file():
        pytest.skip(f"no base interpreter at {base}")
    subprocess.run([str(base), "-m", "venv", "--without-pip", str(root)],
                   check=True, capture_output=True, timeout=180)
    py = root / "bin" / "python"
    probe = subprocess.run([str(py), "-c", "import tokendog"],
                           capture_output=True, timeout=60)
    assert probe.returncode != 0, "fixture is not bare — it can import tokendog"
    return py


def test_bare_python_collides_with_the_target_interpreter(bare_python):
    """Guard the guard.

    The re-exec target and this bare venv must resolve to the SAME binary, so
    that `test_hook_reexecs_and_records_the_event` actually exercises the case
    where comparing realpath() would wrongly skip the exec. If a platform
    copies venv binaries instead of symlinking, this coverage is gone and the
    test above passes for the wrong reason — say so rather than pretend.
    """
    import os
    if os.path.realpath(bare_python) == str(bare_python):
        pytest.skip("this platform copies venv binaries; no symlink collision")
    assert os.path.realpath(bare_python) == os.path.realpath(sys.executable)


def _run(interpreter, script, payload, env, cwd=None):
    """Run a hook. `cwd` defaults to somewhere with no discoverable venv —
    the resolver searches ./.venv, so running from the repo root would let a
    test pass by finding the developer's own environment."""
    return subprocess.run([str(interpreter), str(SCRIPTS / script)],
                          input=json.dumps(payload), text=True,
                          capture_output=True, timeout=60, env=env,
                          cwd=str(cwd) if cwd else str(Path(env["HOME"])))


def test_every_hook_resolves_its_interpreter():
    """A hook that skips the bootstrap is a hook that silently does nothing."""
    for name in HOOKS:
        src = (SCRIPTS / name).read_text()
        assert "from _bootstrap import ensure_tokendog" in src, name
        # It must guard main() before stdin is consumed: a re-exec inherits an
        # unread fd 0 but cannot put back bytes this process already read.
        guard = src.index("if not ensure_tokendog():")
        assert guard < src.index("sys.stdin.read()"), f"{name} reads stdin first"


def test_hook_reexecs_and_records_the_event(bare_python, tmp_path, monkeypatch):
    """The whole point: a wrong interpreter still produces telemetry."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "TOKENDOG_HOME": str(tmp_path / "state"),
           "TOKENDOG_PYTHON": sys.executable}
    r = _run(bare_python, "token_count.py",
             {"hook_event_name": "PostToolUse", "session_id": "reexec",
              "tool_name": "Read", "tool_output": "hello world"}, env)
    assert r.returncode == 0
    written = list((tmp_path / "state").rglob("*.jsonl"))
    assert written, f"no event written; stderr={r.stderr[-400:]}"
    event = json.loads(written[0].read_text().splitlines()[0])
    assert event["session_id"] == "reexec" and event["tool_payload_tokens"] > 0


def test_resolution_is_cached_for_the_next_hook(bare_python, tmp_path):
    """Probing costs a subprocess; a tool call must not pay it every time."""
    state = tmp_path / "state"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "TOKENDOG_HOME": str(state), "TOKENDOG_PYTHON": sys.executable}
    _run(bare_python, "token_count.py",
         {"hook_event_name": "Stop", "session_id": "c",
          "last_assistant_message": "x"}, env)
    cache = state / "interpreter"
    assert cache.is_file()
    assert Path(cache.read_text().strip()).is_file()


def test_stale_cache_is_not_trusted(tmp_path, monkeypatch):
    """A cache pointing at a deleted venv must not pin us to a dead interpreter."""
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    b = _bootstrap()
    (tmp_path / "interpreter").write_text("/nonexistent/bin/python\n")
    assert b._read_cache() is None


def test_reexec_does_not_loop(bare_python, tmp_path):
    """Second pass under a still-broken interpreter must give up, not exec on."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "TOKENDOG_HOME": str(tmp_path / "state"),
           "TOKENDOG_PYTHON": str(bare_python)}  # resolves to itself: still bare
    r = _run(bare_python, "token_count.py",
             {"hook_event_name": "Stop", "session_id": "loop"}, env)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_sink_health_reports_a_dead_interpreter(bare_python, tmp_path):
    """The one hook that is allowed to speak must actually speak."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "TOKENDOG_HOME": str(tmp_path / "state")}  # no TOKENDOG_PYTHON: unresolvable
    r = _run(bare_python, "sink_health.py",
             {"hook_event_name": "SessionStart", "session_id": "s"}, env)
    assert r.returncode == 0
    msg = json.loads(r.stdout)["systemMessage"]
    assert "cannot" in msg and "tokendog" in msg
    assert "TOKENDOG_PYTHON" in msg, "the message must say how to fix it"


def test_silent_hooks_stay_silent_without_tokendog(bare_python, tmp_path):
    """Only sink_health speaks; the rest must not narrate on every tool call."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "TOKENDOG_HOME": str(tmp_path / "state")}
    for name in ("token_count.py", "budget_enforce.py", "truncate_output.py"):
        r = _run(bare_python, name,
                 {"hook_event_name": "PreToolUse", "session_id": "s"}, env)
        assert r.returncode == 0 and r.stdout.strip() == "", name
