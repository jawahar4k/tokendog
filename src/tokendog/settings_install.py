from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .templates import statusline_script

# Writing into a file that is not ours.
#
# A plugin cannot set `statusLine` — plugin settings only carry `agent` and
# `subagentStatusLine` — so it has to be written into the user's own
# `~/.claude/settings.json`. That file holds their model, their permissions,
# their environment. Every rule here exists because getting this wrong costs
# someone their configuration:
#
#   Ownership is exact.     A marker we wrote, compared after normalising
#                           whitespace. A substring match on "tokendog" would
#                           adopt a user's own ~/my-tokendog-statusline.sh,
#                           overwrite it, and delete it on uninstall.
#   Consent is explicit.    A key someone else set is reported and left alone.
#                           `--replace` is the only way past that, and it keeps
#                           what it replaced so `uninstall` can put it back.
#   Invalid JSON is theirs. A file we cannot parse is refused, not reformatted:
#                           parsing loosely and writing back would silently
#                           restyle a file we do not understand.
#   Every write is atomic.  Backup, write a temp file beside it, rename. A
#                           half-written settings.json is a broken session.

MARKER = "tokendog-managed"

# Every key this installer is allowed to touch. Adding one here is the only way
# to have it managed, which keeps "what does tokendog write?" answerable.
MANAGED_KEYS = ("statusLine",)


def settings_path(home=None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return base / ".claude" / "settings.json"


def previous_path(home=None) -> Path:
    return settings_path(home).with_name("tokendog-previous-settings.json")


def desired(key: str) -> dict:
    """What we would write for `key`, marker and all."""
    if key == "statusLine":
        return {"type": "command",
                "command": f"python3 {statusline_script()}  # {MARKER}"}
    raise KeyError(key)


def _normalised(value) -> str:
    """A comparable form, with runs of whitespace inside each string collapsed.

    Normalising the serialised JSON instead would split on the quote characters
    too, so `"  python3 x"` and `"python3 x"` compared equal for the wrong
    reason and `{"a": "b c"}` could match `{"a": "bc"}`.
    """
    def walk(v):
        if isinstance(v, str):
            return " ".join(v.split())
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return json.dumps(walk(value), sort_keys=True)


def _command_target(value) -> Path | None:
    if not isinstance(value, dict):
        return None
    parts = str(value.get("command", "")).split()
    for part in parts:
        if part.endswith(".py"):
            return Path(part)
    return None


def _is_ours(value) -> bool:
    """Ours if it carries our marker, or if it runs the script we ship.

    The second rule exists because `tokendog init` wrote this key for a long
    time with no marker; without it every existing install would be reported as
    a stranger's and blocked. It is safe because it compares the RESOLVED path
    of the script the command runs, not the text of the command — a user's own
    `~/tokendog-statusline.py` is still theirs.
    """
    if not isinstance(value, dict):
        return False
    if MARKER in _normalised(value):
        return True
    target = _command_target(value)
    if target is None:
        return False
    try:
        return target.resolve() == statusline_script().resolve()
    except OSError:
        return False


def _load(home=None):
    """(data, error). A file we cannot parse is an error, never an empty dict."""
    path = settings_path(home)
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"settings.json is invalid JSON and was left untouched: {exc}"
    if not isinstance(data, dict):
        return None, "settings.json is not a JSON object and was left untouched"
    return data, None


def status(home=None) -> dict:
    """Per managed key: none | installed | drifted | broken | other | unreadable."""
    data, error = _load(home)
    if error:
        return {key: "unreadable" for key in MANAGED_KEYS} | {"error": error}
    out = {}
    for key in MANAGED_KEYS:
        current = data.get(key)
        if current is None:
            out[key] = "none"
        elif not _is_ours(current):
            out[key] = "other"
        else:
            # Broken outranks drifted: a command of ours pointing at a script
            # that is gone prints nothing whatever else is true of it, and
            # "drifted" would send the reader looking for the wrong problem.
            target = _command_target(current)
            if target is not None and not target.is_file():
                out[key] = "broken"
            elif _normalised(current) == _normalised(desired(key)):
                out[key] = "installed"
            else:
                out[key] = "drifted"
    return out


def plan(home=None, *, replace: bool = False) -> dict:
    """What `apply` would do, without doing any of it."""
    data, error = _load(home)
    if error:
        return {"error": error}
    out = {}
    for key in MANAGED_KEYS:
        current = data.get(key)
        want = desired(key)
        if current is None:
            action = "install"
        elif not _is_ours(current):
            action = "replace" if replace else "blocked"
        elif _normalised(current) == _normalised(want):
            action = "unchanged"
        else:
            action = "update"
        out[key] = {"action": action, "from": current, "to": want}
    return out


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-tokendog-{stamp}")
    shutil.copy2(path, dest)
    return dest


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os_pid()}")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))   # never rename something unparseable
    tmp.replace(path)


def os_pid() -> int:
    import os
    return os.getpid()


def apply(home=None, *, replace: bool = False) -> dict:
    """Install every managed key. Blocked keys are reported, never forced."""
    p = plan(home, replace=replace)
    if "error" in p:
        return {"applied": False, "error": p["error"], "blocked": [], "plan": p}
    blocked = [k for k, v in p.items() if v["action"] == "blocked"]
    if blocked:
        return {"applied": False, "blocked": blocked, "plan": p,
                "error": "a key you set yourself is in the way"}
    changed = [k for k, v in p.items() if v["action"] in ("install", "update", "replace")]
    if not changed:
        return {"applied": False, "blocked": [], "plan": p, "error": None}

    data, error = _load(home)
    if error:
        return {"applied": False, "error": error, "blocked": [], "plan": p}
    path = settings_path(home)
    backup = _backup(path)

    # Keep what we are about to overwrite, so uninstall can put it back exactly.
    kept = {}
    if previous_path(home).exists():
        try:
            kept = json.loads(previous_path(home).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = {}
    for key in changed:
        current = data.get(key)
        if current is not None and not _is_ours(current) and key not in kept:
            kept[key] = current
        data[key] = desired(key)
    try:
        if kept:
            previous_path(home).parent.mkdir(parents=True, exist_ok=True)
            previous_path(home).write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
        _write(path, data)
    except (OSError, ValueError) as exc:
        return {"applied": False, "error": str(exc), "blocked": [], "plan": p}
    return {"applied": True, "blocked": [], "plan": p, "changed": changed,
            "backup": str(backup) if backup else None, "error": None}


def uninstall(home=None) -> dict:
    """Remove only what we own, restoring anything we displaced."""
    data, error = _load(home)
    if error:
        return {"applied": False, "error": error, "removed": [], "restored": []}
    kept = {}
    if previous_path(home).exists():
        try:
            kept = json.loads(previous_path(home).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = {}
    removed, restored = [], []
    for key in MANAGED_KEYS:
        if not _is_ours(data.get(key)):
            continue                       # not ours: not ours to remove
        if key in kept:
            data[key] = kept.pop(key)
            restored.append(key)
        else:
            data.pop(key, None)
            removed.append(key)
    if not removed and not restored:
        return {"applied": False, "error": None, "removed": [], "restored": []}
    path = settings_path(home)
    _backup(path)
    try:
        _write(path, data)
        if kept:
            previous_path(home).write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
        elif previous_path(home).exists():
            previous_path(home).unlink()
    except (OSError, ValueError) as exc:
        return {"applied": False, "error": str(exc), "removed": [], "restored": []}
    return {"applied": True, "error": None, "removed": removed, "restored": restored}
