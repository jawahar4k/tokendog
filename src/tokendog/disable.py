from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import tokendog_home
from .surface import read_enablement

# Turning a connector off, and back on again.
#
# This is the only module that WRITES the reader's configuration, and it is
# built to be boring about it:
#
#   Dry run by default.   Nothing changes without `--apply`. The default output
#                         is the edit that would be made, named file by file.
#   Always a backup.      The file is copied beside itself, timestamped, before
#                         it is touched, and the path is printed.
#   Always reversible.    The removed configuration is stashed so `--enable`
#                         restores it verbatim. A disable that loses the config
#                         is not a disable, it is a deletion.
#   Never on its own.     There is no automatic mode and no daemon. A report
#                         recommends; a person decides. Disabling a connector
#                         someone needs is worse than paying for one they don't,
#                         and only they know which is which.
#   Refuses on evidence.  A connector with recorded calls is not disabled
#                         without `--force`, because the recommendation engine
#                         and the actuator should not be the same judgement.
#
# Three shapes of connector, three edits:
#   global   remove the entry from `.claude.json` -> `mcpServers`
#   project  add the name to that project's `disabledMcpjsonServers`
#   plugin   set its `enabledPlugins` entry to false in `settings.json`

STASH_NAME = "disabled.json"


def stash_path() -> Path:
    return tokendog_home() / STASH_NAME


def _load(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _backup(path: Path) -> Path:
    dest = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, dest)
    return dest


def _write(path: Path, data: dict) -> None:
    """Write via a temporary file, and parse it before it replaces the original.

    A half-written config is worse than an un-edited one: the harness would
    fail to start rather than merely cost too much.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(path)


def plan(connector: str, *, home=None, enable: bool = False) -> dict:
    """What disabling (or re-enabling) this connector would change.

    Pure: reads config, touches nothing.
    """
    base = Path(home).expanduser() if home else Path.home()
    rec = read_enablement(base)["connectors"].get(connector)
    stash = _load(stash_path()).get(connector)

    if enable:
        if rec and rec.get("enabled"):
            return {"connector": connector, "ok": False,
                    "reason": "already enabled", "edits": []}
        if not rec and not stash:
            return {"connector": connector, "ok": False,
                    "reason": "not found in config and nothing stashed", "edits": []}
    elif rec is None:
        return {"connector": connector, "ok": False,
                "reason": "not found in config — nothing to disable", "edits": []}
    elif not rec.get("enabled"):
        return {"connector": connector, "ok": False,
                "reason": "already disabled", "edits": []}

    scope = (rec or {}).get("scope") or (stash or {}).get("scope") or "unknown"
    projects = list((rec or {}).get("projects") or (stash or {}).get("projects") or [])
    plugin_key = ((rec or {}).get("source") or (stash or {}).get("source") or "")
    plugin_key = plugin_key.replace("plugin ", "") if plugin_key.startswith("plugin ") else ""
    edits = []
    if scope == "global":
        target = base / ".claude.json"
        edits.append({
            "file": str(target),
            "change": (f"restore mcpServers['{connector}']" if enable
                       else f"remove mcpServers['{connector}'] (stashed for restore)"),
        })
    elif scope == "project":
        target = base / ".claude.json"
        for project in projects:
            edits.append({
                "file": str(target),
                "change": (f"remove '{connector}' from disabledMcpjsonServers of {project}"
                           if enable else
                           f"add '{connector}' to disabledMcpjsonServers of {project}"),
            })
    elif scope == "plugin":
        target = base / ".claude" / "settings.json"
        if not plugin_key:
            return {"connector": connector, "ok": False,
                    "reason": "cannot tell which plugin owns this connector", "edits": []}
        edits.append({
            "file": str(target),
            "change": f"set enabledPlugins['{plugin_key}'] = {str(enable).lower()}",
        })
    else:
        return {"connector": connector, "ok": False,
                "reason": f"scope '{scope}' cannot be edited automatically", "edits": []}

    return {"connector": connector, "ok": True, "reason": None,
            "scope": scope, "enable": enable, "edits": edits,
            "projects": projects, "plugin_key": plugin_key}


def apply(connector: str, *, home=None, enable: bool = False) -> dict:
    """Carry out the plan. Backs up every file it touches, first."""
    p = plan(connector, home=home, enable=enable)
    if not p["ok"]:
        return p | {"applied": False, "backups": []}

    base = Path(home).expanduser() if home else Path.home()
    scope = p["scope"]
    backups: list[str] = []
    stash_file = stash_path()
    stash = _load(stash_file)

    if scope in ("global", "project"):
        target = base / ".claude.json"
        if not target.is_file():
            return p | {"applied": False, "backups": [],
                        "ok": False, "reason": f"{target} is missing"}
        backups.append(str(_backup(target)))
        data = _load(target)

        if scope == "global":
            servers = data.setdefault("mcpServers", {})
            if enable:
                saved = (stash.get(connector) or {}).get("config")
                if saved is None:
                    return p | {"applied": False, "backups": backups, "ok": False,
                                "reason": "no stashed config to restore"}
                servers[connector] = saved
                stash.pop(connector, None)
            else:
                stash[connector] = {"scope": "global", "config": servers.get(connector),
                                    "stashed_at": datetime.now(timezone.utc).isoformat()}
                servers.pop(connector, None)
        else:
            wanted = set(p["projects"])
            for path, cfg in (data.get("projects") or {}).items():
                if not isinstance(cfg, dict) or Path(path).name not in wanted:
                    continue
                lst = cfg.setdefault("disabledMcpjsonServers", [])
                if enable:
                    cfg["disabledMcpjsonServers"] = [x for x in lst if x != connector]
                elif connector not in lst:
                    lst.append(connector)
            stash[connector] = {"scope": "project", "projects": p["projects"],
                                "stashed_at": datetime.now(timezone.utc).isoformat()}
        _write(target, data)

    elif scope == "plugin":
        target = base / ".claude" / "settings.json"
        if not target.is_file():
            return p | {"applied": False, "backups": [], "ok": False,
                        "reason": f"{target} is missing"}
        backups.append(str(_backup(target)))
        data = _load(target)
        key = p["plugin_key"]
        data.setdefault("enabledPlugins", {})[key] = bool(enable)
        _write(target, data)
        stash[connector] = {"scope": "plugin", "source": f"plugin {key}",
                            "stashed_at": datetime.now(timezone.utc).isoformat()}

    stash_file.parent.mkdir(parents=True, exist_ok=True)
    stash_file.write_text(json.dumps(stash, indent=1) + "\n", encoding="utf-8")
    return p | {"applied": True, "backups": backups}


def format_plan(p: dict, *, applied: bool = False) -> str:
    verb = "Re-enable" if p.get("enable") else "Disable"
    if not p["ok"]:
        return f"{verb} `{p['connector']}`: {p['reason']}"
    lines = [f"{verb} `{p['connector']}` ({p['scope']}):"]
    for e in p["edits"]:
        lines.append(f"  {e['file']}")
        lines.append(f"    {e['change']}")
    if applied:
        for b in p.get("backups", []):
            lines.append(f"  backup: {b}")
        lines.append("Applied. Restart any running session for it to take effect.")
    else:
        lines.append("Dry run — nothing changed. Add --apply to make these edits.")
    return "\n".join(lines)
