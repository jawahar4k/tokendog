"""Find a Python that can import `tokendog`, and re-exec under it if needed.

Hooks are invoked as `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/<hook>.py`, and the
MCP server the same way. `python3` is whatever is first on PATH, which is
frequently NOT the environment `pip install tokendog` wrote to — a project
venv, a pyenv shim, Homebrew python vs `/usr/bin/python3`.

Every hook deliberately swallows the resulting ImportError and exits 0, because
a hook must never crash the session. That is the right trade, but it means the
symptom of a wrong interpreter is not an error message: it is a plugin that
runs on every tool call, forever, and records nothing. `/tokendog:cost` then
reports $0.00 with total confidence.

Resolution order is cheapest-first, and NOTHING here spawns a subprocess unless
the default interpreter has already failed — a correctly installed setup pays
only one `import tokendog` that was going to happen anyway.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Set on the re-exec'd process so a python that still cannot import tokendog
# (a stale cache, a broken venv) fails once instead of exec'ing forever.
REEXEC_FLAG = "TOKENDOG_HOOK_REEXEC"
PROBE = "import tokendog"


def _home() -> Path:
    """Mirror `tokendog.sink.tokendog_home` — which we cannot import yet."""
    return Path(os.environ.get("TOKENDOG_HOME") or Path.home() / ".tokendog")


def cache_path() -> Path:
    return _home() / "interpreter"


def _usable(candidate) -> bool:
    """True if `candidate` is an executable that can import tokendog."""
    if not candidate:
        return False
    p = Path(candidate)
    if not (p.is_file() and os.access(p, os.X_OK)):
        return False
    try:
        return subprocess.run([str(p), "-c", PROBE], timeout=15,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


def _from_console_script() -> str | None:
    """The `tokendog` console script's shebang names the interpreter pip used."""
    script = shutil.which("tokendog")
    if not script:
        return None
    try:
        with open(script, "rb") as f:
            first = f.readline(512).decode("utf-8", "replace").strip()
    except Exception:
        return None
    return first[2:].split(None, 1)[0] if first.startswith("#!") else None


def candidates() -> list[str]:
    """Interpreters worth probing, most-likely and cheapest first."""
    out: list[str] = []

    def add(value) -> None:
        if value and str(value) not in out:
            out.append(str(value))

    add(os.environ.get("TOKENDOG_PYTHON"))
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        add(Path(venv) / "bin" / "python")
    for root in (os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if not root:
            continue
        for name in (".venv", "venv"):
            add(Path(root) / name / "bin" / "python")
    add(_from_console_script())
    return out


def _read_cache() -> str | None:
    try:
        value = cache_path().read_text(encoding="utf-8").strip()
    except Exception:
        return None
    # A cache pointing at a deleted venv must not pin us to a dead interpreter.
    return value if value and Path(value).is_file() else None


def _write_cache(interpreter: str) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(interpreter + "\n", encoding="utf-8")
    except Exception:
        pass  # the cache is an optimisation; never let it break a hook


def resolve_interpreter() -> str | None:
    """Return a Python that can import tokendog, or None. Caches the answer."""
    cached = _read_cache()
    if cached and _usable(cached):
        return cached
    for candidate in candidates():
        if _usable(candidate):
            _write_cache(candidate)
            return candidate
    return None


def ensure_tokendog() -> bool:
    """True if `import tokendog` works here.

    If it does not, re-exec this same script under an interpreter where it does
    and never return. Call this BEFORE reading stdin: a re-exec inherits an
    unread fd 0, but cannot put back bytes this process already consumed.
    """
    try:
        import tokendog  # noqa: F401
        return True
    except Exception:
        pass
    if os.environ.get(REEXEC_FLAG):
        return False
    interpreter = resolve_interpreter()
    # abspath, NOT realpath: every venv symlinks its `python` to the same base
    # interpreter, so realpath() reports a project venv and Homebrew python3 as
    # identical and would skip the exec in exactly the case this exists for.
    # A venv's identity is its path, not its symlink target.
    if not interpreter or os.path.abspath(interpreter) == os.path.abspath(sys.executable):
        return False
    env = dict(os.environ, **{REEXEC_FLAG: "1"})
    try:
        os.execve(interpreter, [interpreter, *sys.argv], env)
    except Exception:
        return False
    return False  # unreachable when execve succeeds
