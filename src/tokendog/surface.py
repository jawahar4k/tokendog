from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .approx import approx_tokens
from .config import tokendog_home
from .event import SOURCE_HOOK

# The composition of the context window: what is in every prompt before the
# conversation even starts, what it costs, and whether any of it earned its place.
#
# The other detectors all look at the window's SIZE. This one looks at its
# CONTENTS. A tool definition is resident on every turn of every session where
# its connector is enabled, whether or not the tool is ever called — so an
# unused connector is a fixed tax on the whole corpus, and the cheapest possible
# saving because removing it costs nothing but a config edit.
#
# GROUPED BY CONNECTOR, not by tool. A connector is the unit you can actually
# turn off: an MCP server contributes all of its tools or none of them, so a
# per-tool list is unreadable and unactionable while a per-connector total with
# its tools underneath is both.
#
# WHAT IS EXACT AND WHAT IS NOT
#   Exact, from transcripts:  which tools were called, how often, in which
#                             project, and how many turns ran there.
#   Exact, from config:       which connectors are enabled, and at what scope.
#   NOT in the data:          the token size of a tool's schema. The transcript
#                             keeps usage and content, never the request's
#                             `tools` array. Sizes therefore come from an
#                             inventory captured separately (see `--refresh`),
#                             and until one exists this reports residency and
#                             usage without a token figure rather than guessing.
#   Approximate by nature:    residency. Nothing records which connectors a
#                             PAST session had, so a connector is credited with
#                             the turns of the projects it is configured for.
#                             Correct for a stable config, an over-estimate for
#                             one enabled recently. Stated, not hidden.
#
# Skills are priced in two parts because they are billed in two ways: the name
# and description sit in every prompt (always-on), while the body is only read
# when the skill is invoked (on-demand). Conflating them overstates a large
# skill's cost several times over.

INVENTORY_NAME = "surface.json"

# A connector earning fewer calls than this per 1,000 resident turns is doing
# almost nothing for what it costs. Not zero — zero is its own verdict — but
# rare enough to be worth a look.
LOW_USE_PER_1K_TURNS = 1.0


def inventory_path() -> Path:
    return tokendog_home() / INVENTORY_NAME


def parse_mcp_tool(name: str) -> tuple[str, str] | None:
    """`mcp__<connector>__<tool>` -> (connector, tool), or None if not MCP.

    Split from the left on the double underscore, exactly twice: a connector
    name routinely contains single underscores (`plugin_context7_context7`) and
    a tool name routinely contains hyphens (`resolve-library-id`), so anything
    cleverer than this gets both wrong.
    """
    if not isinstance(name, str) or not name.startswith("mcp__"):
        return None
    parts = name.split("__", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_enablement(home=None) -> dict:
    """Which connectors are configured, and where.

    Reads only; never connects to anything. Global servers are in scope for
    every project; a project entry scopes its own.
    """
    base = Path(home).expanduser() if home else Path(os.path.expanduser("~"))
    root = _read_json(base / ".claude.json")
    settings = _read_json(base / ".claude" / "settings.json")

    connectors: dict[str, dict] = {}
    for name, cfg in (root.get("mcpServers") or {}).items():
        connectors[name] = {"connector": name, "scope": "global", "projects": [],
                            "enabled": True, "source": ".claude.json",
                            "config": cfg if isinstance(cfg, dict) else {}}

    for path, cfg in (root.get("projects") or {}).items():
        if not isinstance(cfg, dict):
            continue
        project = Path(path).name
        disabled = set(cfg.get("disabledMcpjsonServers") or [])
        for name, scfg in (cfg.get("mcpServers") or {}).items():
            rec = connectors.setdefault(name, {
                "connector": name, "scope": "project", "projects": [],
                "enabled": True, "source": ".claude.json:projects",
                "config": scfg if isinstance(scfg, dict) else {}})
            if not rec.get("config") and isinstance(scfg, dict):
                rec["config"] = scfg
            if project not in rec["projects"]:
                rec["projects"].append(project)
            if name in disabled:
                rec["enabled"] = False
        for name in (cfg.get("enabledMcpjsonServers") or []):
            rec = connectors.setdefault(name, {
                "connector": name, "scope": "project", "projects": [],
                "enabled": True, "source": ".mcp.json", "config": {}})
            if project not in rec["projects"]:
                rec["projects"].append(project)

    plugins = {name: bool(on)
               for name, on in (settings.get("enabledPlugins") or {}).items()}

    # A plugin can ship its own MCP server. It is namespaced
    # `plugin_<plugin>_<server>` in tool names, and declared either as a
    # `.mcp.json` or as files under an `mcpServers/` directory in the plugin's
    # cached copy — so both are read. Without this these connectors show up
    # only because a tool was called, with no scope and no enablement.
    cache = base / ".claude" / "plugins" / "cache"
    if cache.is_dir():
        for decl in sorted(cache.glob("*/*/*/.mcp.json")):
            plugin = decl.parents[1].name
            marketplace = decl.parents[2].name
            for server, scfg in (_read_json(decl).get("mcpServers") or {}).items():
                _add_plugin_connector(connectors, plugins, plugin, marketplace, server,
                                      scfg if isinstance(scfg, dict) else {})
        for d in sorted(cache.glob("*/*/*/mcpServers")):
            if not d.is_dir():
                continue
            plugin = d.parents[1].name
            marketplace = d.parents[2].name
            for f in sorted(d.iterdir()):
                if f.suffix in (".json", ".yaml", ".yml") or f.is_dir():
                    cfg = _read_json(f) if f.suffix == ".json" else {}
                    _add_plugin_connector(connectors, plugins, plugin, marketplace,
                                          f.stem, cfg.get("mcpServers", {}).get(f.stem) or cfg)

    return {"connectors": connectors, "plugins": plugins}


def _add_plugin_connector(connectors: dict, plugins: dict, plugin: str,
                          marketplace: str, server: str, config=None) -> None:
    name = f"plugin_{plugin}_{server}"
    key = f"{plugin}@{marketplace}"
    rec = connectors.setdefault(name, {
        "connector": name, "scope": "plugin", "projects": [],
        "enabled": True, "source": f"plugin {key}", "config": config or {}})
    if config and not rec.get("config"):
        rec["config"] = config
    rec["scope"] = "plugin"
    rec["source"] = f"plugin {key}"
    # A plugin absent from enabledPlugins has never been switched on.
    rec["enabled"] = bool(plugins.get(key, False))


def _frontmatter_and_body(text: str) -> tuple[str, str]:
    """Split a skill file into its always-on header and its on-demand body."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[:end + 4], text[end + 4:]
    return "", text


def local_surface(home=None) -> dict:
    """Skills, plugin commands and instruction files on disk, priced.

    Token counts are a tiktoken approximation of the file's text, which is what
    `approx.py` exists for. It is the right order of magnitude for deciding what
    to remove; it is not a billing figure, and nothing here is priced in dollars.
    """
    base = Path(home).expanduser() if home else Path(os.path.expanduser("~"))
    out: dict[str, list] = {"skills": [], "commands": [], "instructions": []}

    # Skills nest: a bundle directory can hold many skill directories, so the
    # search is recursive rather than one level deep.
    root = base / ".claude" / "skills"
    if root.is_dir():
        for f in sorted(root.rglob("SKILL.md")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            head, body = _frontmatter_and_body(text)
            rel = f.parent.relative_to(root)
            out["skills"].append({
                "name": str(rel),
                "always_on_tokens": approx_tokens(head),
                "on_demand_tokens": approx_tokens(body),
            })

    cache = base / ".claude" / "plugins" / "cache"
    if cache.is_dir():
        for md in sorted(cache.glob("*/*/commands/*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out["commands"].append({
                "name": md.stem, "plugin": md.parents[1].name,
                "tokens": approx_tokens(text)})

    for f in (base / ".claude" / "CLAUDE.md", ):
        if f.is_file():
            try:
                out["instructions"].append({
                    "name": str(f).replace(str(base), "~"),
                    "tokens": approx_tokens(f.read_text(encoding="utf-8", errors="replace"))})
            except OSError:
                pass
    return out


def load_inventory(path=None) -> dict:
    """Tool schema sizes captured by a previous `--refresh`, or empty."""
    p = Path(path) if path else inventory_path()
    data = _read_json(p)
    tools = data.get("tools")
    return data if isinstance(tools, dict) else {}


def save_inventory(tools: dict, path=None, *, merge: bool = True) -> Path:
    """Persist probed tool-schema sizes.

    MERGES by default: a size once measured is kept even if a later probe could
    not reach that server (a connector that failed to start this run, a timed-out
    credential prompt). Replacing wholesale — the old behaviour — meant one flaky
    re-probe erased a good measurement and the floor silently under-reported.
    New sizes win per key; connectors absent from this run keep their prior size.
    Pass merge=False for a clean rebuild.
    """
    p = Path(path) if path else inventory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(tools)
    if merge:
        try:
            prior = load_inventory(p).get("tools", {})
        except Exception:
            prior = {}
        merged = {**prior, **tools}   # new wins; prior preserved for un-reprobed servers
    p.write_text(json.dumps({
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "tools": merged,
    }, indent=1) + "\n", encoding="utf-8")
    return p


def _verdict(*, enabled: bool, calls: int, turns: int) -> tuple[str, str]:
    """A verdict, and the sentence that justifies it.

    Zero residency is its own case: a connector scoped to a project with no
    sessions in the corpus has not been shown to be useless, only unexercised.
    Recommending a disable on no evidence would be the one thing that makes a
    tool like this untrustworthy.
    """
    if not enabled:
        return "off", "Already disabled"
    if turns == 0:
        return "nodata", "No sessions in scope — nothing measured"
    if calls == 0:
        return "unused", "Never called — disable the connector"
    if (calls / turns) * 1000 < LOW_USE_PER_1K_TURNS:
        return "low", "Rarely called — review"
    return "used", "Earning its place"


def surface_summary(events: Iterable, *, home=None, inventory=None,
                    since=None) -> dict:
    """Group the tool surface by connector, with usage, residency and verdict."""
    enablement = read_enablement(home)
    inv = (inventory if inventory is not None else load_inventory()).get("tools", {}) \
        if isinstance(inventory, dict) or inventory is None else {}

    calls: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    builtin: dict[str, int] = defaultdict(int)
    turns_by_project: dict[str, int] = defaultdict(int)
    projects_seen: set[str] = set()
    total_turns = 0

    for e in events:
        project = getattr(e, "project", None)
        if getattr(e, "is_turn", False):
            if since is not None:
                ts = getattr(e, "ts", None)
                if not ts or str(ts) < str(since):
                    continue
            total_turns += 1
            if project:
                turns_by_project[project] += 1
                projects_seen.add(project)
        # Transcript turns carry `tools` (every tool the response invoked);
        # hook events carry a single `tool`. Both are counted, and a hook event
        # is not a turn so it cannot inflate residency.
        invoked = list(getattr(e, "tools", None) or [])
        single = getattr(e, "tool", None)
        if single and getattr(e, "source", None) == SOURCE_HOOK:
            invoked.append(single)
        for tool in invoked:
            parsed = parse_mcp_tool(tool)
            if parsed:
                calls[parsed[0]][parsed[1]] += 1
            else:
                builtin[tool] += 1

    def residency(rec: dict) -> int:
        if rec["scope"] == "global" or not rec["projects"]:
            return total_turns
        return sum(turns_by_project.get(p, 0) for p in rec["projects"])

    connectors = []
    known = set(enablement["connectors"]) | set(calls)
    for name in sorted(known):
        rec = enablement["connectors"].get(name, {
            "connector": name, "scope": "unknown", "projects": [],
            "enabled": True, "source": "observed in transcripts", "config": {}})
        used = calls.get(name, {})
        turns = residency(rec)
        n_calls = sum(used.values())
        prefix = f"mcp__{name}__"
        sized = {k: v for k, v in inv.items() if k.startswith(prefix)}
        schema = sum(int(v or 0) for v in sized.values()) or None
        # A connector can be well used and still bloated: every tool it ships
        # is resident, so the ones never called are dead weight inside a
        # connector you cannot simply switch off.
        known = len(sized) or None
        idle = ([t[len(prefix):] for t in sorted(sized)
                 if t[len(prefix):] not in used] if sized else [])
        severity, action = _verdict(enabled=rec["enabled"], calls=n_calls, turns=turns)
        connectors.append({
            "connector": name,
            "scope": rec["scope"],
            "projects": rec["projects"],
            "enabled": rec["enabled"],
            "source": rec["source"],
            "probeable": bool((rec.get("config") or {}).get("command")
                              or (rec.get("config") or {}).get("url")),
            "tools_known": known,
            "tools_called": len(used),
            "tools_idle": idle,
            "calls": n_calls,
            "turns_resident": turns,
            "calls_per_1k_turns": round((n_calls / turns) * 1000, 2) if turns else None,
            "schema_tokens": schema,
            "carried_tokens": schema * turns if schema else None,
            "severity": severity,
            "action": action,
            "tools": sorted(({"tool": t, "calls": c} for t, c in used.items()),
                            key=lambda r: -r["calls"]),
        })
    # Worst first: unused-but-enabled before rarely-used before earning.
    order = {"unused": 0, "low": 1, "used": 2, "nodata": 3, "off": 4}
    connectors.sort(key=lambda c: (order.get(c["severity"], 9),
                                   -(c["carried_tokens"] or 0), -c["turns_resident"]))

    local = local_surface(home)
    always_on = sum(s["always_on_tokens"] for s in local["skills"]) \
        + sum(i["tokens"] for i in local["instructions"])

    return {
        "connectors": connectors,
        "builtin_tools": sorted(({"tool": t, "calls": c} for t, c in builtin.items()),
                                key=lambda r: -r["calls"]),
        "local": local,
        "plugins": enablement["plugins"],
        "totals": {
            "connectors": len(connectors),
            "unused": sum(1 for c in connectors if c["severity"] == "unused"),
            "low_use": sum(1 for c in connectors if c["severity"] == "low"),
            "turns": total_turns,
            "projects": len(projects_seen),
            "always_on_local_tokens": always_on,
            "carried_by_unused": sum(c["carried_tokens"] or 0 for c in connectors
                                     if c["severity"] == "unused"),
            "have_inventory": bool(inv),
        },
        "thresholds": {"low_use_per_1k_turns": LOW_USE_PER_1K_TURNS},
    }


def probe_configs(home=None) -> dict:
    """Connector -> launch config, for the connectors that can be started.

    A connector observed only in a transcript has no config to launch, and a
    disabled one is deliberately not started.
    """
    out = {}
    for name, rec in read_enablement(home)["connectors"].items():
        cfg = rec.get("config") or {}
        if rec.get("enabled") and (cfg.get("command") or cfg.get("url")):
            out[name] = cfg
    return out


def surface_report_data(transcript_root=None, project=None, home=None, *, window=None) -> dict:
    """Read transcripts + the sink and summarise the surface."""
    from itertools import chain
    from .sink import read_events
    from .transcripts import read_transcripts
    from .window import scoped
    events = chain(read_transcripts(transcript_root), read_events())
    if project:
        events = (e for e in events if getattr(e, "project", None) == project)
    events = scoped(events, window)
    data = surface_summary(events, home=home)
    data["project"] = project
    data["window"] = window.label if window is not None else None
    return data
