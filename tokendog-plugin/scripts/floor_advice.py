#!/usr/bin/env python3
"""SessionStart hook: point out unused MCP connectors that cost tokens every turn.

The single clearest win tokendog finds is a connector that is enabled, sits in
every turn's context as tool schemas, and is never called — pure baseline paid
for nothing. Nobody runs a report to discover that, so this says it where the
work happens: once, at the start of a session, with the reversible fix named so
the assistant can act on a plain "yes, disable them" — no command for the user
to type, no config touched without their say-so.

Restraint, because a notice that fires every session is a notice nobody reads:
  - Speaks only when the waste is worth it: reclaimable baseline over a floor,
    at least one never-called connector.
  - At most once a day (throttled via a state file), and never when the reader
    has asked for quiet (TOKENDOG_QUIET) or opted out (TOKENDOG_ADVICE=0).
  - Reads only — it never changes config. The fix is offered, not applied.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _bootstrap import ensure_tokendog
except Exception:  # pragma: no cover
    def ensure_tokendog() -> bool:
        return True

MIN_RECLAIMABLE = 500       # tokens/turn below which it is not worth a line
THROTTLE_HOURS = 24.0       # at most one nudge a day


def _off(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in ("0", "false", "no", "off")


def _quiet() -> bool:
    return str(os.environ.get("TOKENDOG_QUIET", "")).strip().lower() in ("1", "true", "yes", "on")


def _human(n: int) -> str:
    n = int(n or 0)
    if n < 1_000:
        return str(n)
    if n < 10_000:
        return f"{n / 1_000:.1f}K"
    return f"{n / 1_000_000:.2f}M" if n >= 1_000_000 else f"{n / 1_000:.0f}K"


def main() -> int:
    if _off("TOKENDOG_ADVICE") or _quiet():
        return 0
    if not ensure_tokendog():
        return 0
    # "Unused" is PER PROJECT: a connector idle in this repo may be used heavily
    # in another, so the judgement must be scoped to the session's own project —
    # not the whole machine. The cwd comes in the SessionStart payload.
    project = None
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        cwd = payload.get("cwd") or os.getcwd()
        project = os.path.basename(str(cwd).rstrip("/")) or None
    except Exception:
        project = None
    try:
        from tokendog.config import tokendog_home
        # Throttle per project, so each repo can nudge once a day on its own terms.
        state_path = tokendog_home() / "advice.json"
        now = time.time()
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        seen = state.get("last_shown", {})
        if not isinstance(seen, dict):
            seen = {}
        if now - float(seen.get(project or "_", 0) or 0) < THROTTLE_HOURS * 3600:
            return 0

        from tokendog.floor import floor_budget
        data = floor_budget(project=project)
        # Never-called connectors that actually carry a measured schema.
        dead = [i for i in data["items"]
                if i["kind"] == "mcp" and i["verdict"] == "reclaim" and (i["per_turn"] or 0) > 0]
        reclaimable = sum(i["per_turn"] for i in dead)
        if not dead or reclaimable < MIN_RECLAIMABLE:
            return 0

        dead.sort(key=lambda i: -(i["per_turn"] or 0))
        named = ", ".join(f"{i['item']} ({_human(i['per_turn'])})" for i in dead[:3])
        more = f" +{len(dead) - 3} more" if len(dead) > 3 else ""
        where = f" in {project}" if project else ""
        # The command is included so the ASSISTANT can act on "yes" — the reader
        # never types it. Re-enable is named so the offer is visibly reversible.
        first = dead[0]["item"]
        msg = (f"TokenDog: ~{_human(reclaimable)} tokens/turn go to MCP connector(s) never called"
               f"{where} — {named}{more} — resident in every turn here. "
               f"Safe to disable and fully reversible. Want them gone? I can run "
               f"`tokendog surface --disable {first} --apply` (undo: `--enable {first}`), "
               f"or see all in the dashboard's Floor tab. Set TOKENDOG_ADVICE=0 to mute this.")
        print(json.dumps({"systemMessage": msg}))

        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            seen[project or "_"] = now
            state["last_shown"] = seen
            state_path.write_text(json.dumps(state), encoding="utf-8")
        except Exception:
            pass
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
