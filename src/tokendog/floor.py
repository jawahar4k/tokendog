from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .surface import local_surface, surface_summary
from .transcripts import transcript_root

# The context floor budget: every invisible thing that rides in EVERY turn's
# window before you type a word — MCP tool schemas, skills' always-on
# frontmatter, instruction files, the system prompt — itemised, sized, and
# marked used or dead-weight.
#
# WHY THIS IS ITS OWN VIEW. `surface` already sizes connectors and `session`
# shows the floor as four rollup lines. Neither answers the plain question:
# "what is in my floor, line by line, and which lines am I paying for and not
# using?" A 1.27M-token MCP that is never called is invisible in the context
# ledger — it just makes every turn a little heavier — until it is put on one
# list beside a "0 calls" and a verdict.
#
# THE COST MODEL. A floor item is not paid once; it is re-read on EVERY turn of
# EVERY session, because it sits in the cached prefix each request carries. So
# its real weight is `size × turns`, and a small schema resident across
# thousands of turns can outweigh a large one used briefly. The report shows the
# per-turn size (what you would reclaim) and the carried total (what it has cost
# so far) side by side.
#
# USED vs DEAD-WEIGHT.
#   MCP connector — used if its tools were called; dead if resident and never
#                   called (reclaim by disabling the connector).
#   skill         — its FRONTMATTER is always-on floor whether or not the skill
#                   is invoked; used if the Skill tool loaded its body at least
#                   once. Always-on tokens for a never-invoked skill are pure
#                   dead weight.
#   instruction   — CLAUDE.md and the like are always in context by design; that
#                   is their job, so they are floor-but-earned, flagged only if
#                   unusually large.
#   system        — the remainder (system prompt + seed). Not removable; shown so
#                   the budget sums to the real first-turn context.

# Rough line past which an always-on item is worth a second look.
SKILL_HEAVY = 400          # always-on tokens for one skill
INSTRUCTION_HEAVY = 4_000  # a CLAUDE.md this big is worth trimming


def skill_invocations(transcript_root_path=None, *, project=None) -> dict:
    """How many times each skill's body was loaded via the Skill tool.

    A skill's frontmatter is always in context; its body loads only on
    invocation. So invocation count is exactly "did this skill earn the
    always-on cost it charges every turn". Matched by the skill name in the
    Skill tool_use input, against the skill directory names on disk.
    """
    base = Path(transcript_root_path).expanduser() if transcript_root_path else transcript_root()
    counts: dict[str, int] = defaultdict(int)
    if not base.exists():
        return counts
    for path in base.rglob("*.jsonl"):
        cwd_name = None
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"Skill"' not in line and "'Skill'" not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                cwd = rec.get("cwd")
                if isinstance(cwd, str) and cwd and cwd_name is None:
                    cwd_name = Path(cwd.rstrip("/")).name
                if project and cwd_name and cwd_name != project:
                    continue
                msg = rec.get("message")
                if rec.get("type") != "assistant" or not isinstance(msg, dict):
                    continue
                for b in (msg.get("content") or []):
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    if b.get("name") != "Skill":
                        continue
                    inp = b.get("input") or {}
                    name = inp.get("skill") or inp.get("command") or inp.get("name")
                    if isinstance(name, str) and name.strip():
                        counts[name.strip()] += 1
    return counts


def _skill_used(skill_name: str, invoked: dict) -> int:
    """Invocations of a skill, matching a disk name against the tool's arg.

    The disk name is a path (`infinite/git-workflow`); the tool is invoked by a
    slug (`git-workflow` or the full path or a plugin-qualified name). Match on
    the last path segment as well as the whole, so either spelling counts.
    """
    if skill_name in invoked:
        return invoked[skill_name]
    leaf = skill_name.rsplit("/", 1)[-1]
    total = 0
    for k, v in invoked.items():
        kl = k.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if kl == leaf or k == leaf or k.endswith("/" + skill_name):
            total += v
    return total


def floor_sizes(home=None) -> dict:
    """The always-on floor SIZE by kind — cheap: config + disk, no transcript scan.

    Just the token weight each kind adds to every turn, for a statusline or a
    quick "what's my baseline". Usage/verdicts need `floor_budget` (which does
    scan). MCP is summed from the cached inventory for ENABLED connectors only —
    a schema is floor only while its connector is on — so it is zero until
    `surface --refresh` has measured schemas.
    """
    from .surface import load_inventory, parse_mcp_tool, read_enablement
    inv = load_inventory().get("tools", {})
    enabled = {n for n, r in read_enablement(home)["connectors"].items()
               if r.get("enabled")}
    mcp = 0
    for tool_key, size in inv.items():
        parsed = parse_mcp_tool(tool_key)
        conn = parsed[0] if parsed else None
        if conn and conn in enabled:
            mcp += int(size or 0)
    local = local_surface(home)
    skill = sum(s["always_on_tokens"] for s in local["skills"])
    instruction = sum(i["tokens"] for i in local["instructions"])
    return {"mcp": mcp, "skill": skill, "instruction": instruction,
            "total": mcp + skill + instruction,
            "have_inventory": bool(inv)}


def floor_budget(transcript_root_path=None, *, project=None, home=None,
                 window=None) -> dict:
    """One itemised budget of the always-resident context, with verdicts."""
    from itertools import chain
    from .sink import read_events
    from .transcripts import read_transcripts
    from .window import scoped

    events = chain(read_transcripts(transcript_root_path), read_events())
    if project:
        events = (e for e in events if getattr(e, "project", None) == project)
    events = scoped(events, window)
    surf = surface_summary(events, home=home)
    local = local_surface(home)
    invoked = skill_invocations(transcript_root_path, project=project)

    items: list[dict] = []

    # --- MCP connectors (schema resident every turn) ---
    for c in surf["connectors"]:
        size = c.get("schema_tokens")
        used = (c.get("calls") or 0) > 0
        resident = c.get("turns_resident") or 0
        if not c.get("enabled"):
            verdict = "off"
        elif size is None:
            verdict = "unmeasured"          # needs `surface --refresh` to size
        elif not used and resident:
            verdict = "reclaim"             # resident, never called → dead weight
        elif not used:
            verdict = "idle"
        else:
            verdict = "keep"
        items.append({
            "item": c["connector"], "kind": "mcp", "scope": c.get("scope"),
            "per_turn": size, "used": used, "calls": c.get("calls") or 0,
            "turns_resident": resident,
            "carried": c.get("carried_tokens"),
            "detail": (f"{c.get('tools_called', 0)}/{c.get('tools_known')} tools called"
                       if c.get("tools_known") else f"{c.get('calls') or 0} calls"),
            "verdict": verdict,
        })

    # --- skills (frontmatter always-on; body on invoke) ---
    for sk in local["skills"]:
        on = sk["always_on_tokens"]
        n = _skill_used(sk["name"], invoked)
        if on <= 0:
            verdict = "keep"                # nothing always-on to reclaim
        elif n == 0:
            verdict = "reclaim"             # pays frontmatter every turn, never invoked
        elif on >= SKILL_HEAVY:
            verdict = "trim"                # used, but a heavy always-on block
        else:
            verdict = "keep"
        items.append({
            "item": sk["name"], "kind": "skill", "scope": "local",
            "per_turn": on, "used": n > 0, "calls": n,
            "turns_resident": None, "carried": None,
            "detail": (f"invoked {n}×" if n else "never invoked")
                      + f" · +{sk['on_demand_tokens']} on load",
            "verdict": verdict,
        })

    # --- instruction files (always in context by design) ---
    for ins in local["instructions"]:
        tok = ins["tokens"]
        verdict = "trim" if tok >= INSTRUCTION_HEAVY else "keep"
        items.append({
            "item": ins["name"], "kind": "instruction", "scope": "local",
            "per_turn": tok, "used": True, "calls": None,
            "turns_resident": None, "carried": None,
            "detail": "always in context", "verdict": verdict,
        })

    # Order: reclaimable first (what to act on), then by per-turn size.
    rank = {"reclaim": 0, "trim": 1, "idle": 2, "unmeasured": 3, "keep": 4, "off": 5}
    items.sort(key=lambda i: (rank.get(i["verdict"], 9), -(i["per_turn"] or 0)))

    measured = [i for i in items if i["per_turn"] is not None]
    floor_known = sum(i["per_turn"] for i in measured)
    reclaimable = sum(i["per_turn"] for i in items
                      if i["verdict"] == "reclaim" and i["per_turn"])
    carried_wasted = sum(i["carried"] for i in items
                         if i["verdict"] == "reclaim" and i["carried"])
    return {
        "items": items,
        "totals": {
            "items": len(items),
            "floor_known": floor_known,              # sum of what we could measure
            "reclaimable_per_turn": reclaimable,     # freed from every future turn if removed
            "carried_wasted": carried_wasted,        # already spent on dead weight
            "reclaim_items": sum(1 for i in items if i["verdict"] == "reclaim"),
            "have_inventory": bool(surf.get("totals", {}).get("have_inventory")),
        },
        "project": project,
        "window": window.label if window is not None else None,
    }
