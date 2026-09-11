#!/usr/bin/env python3
"""SessionStart hook: keep the MCP tool-schema inventory fresh, without a command.

The statusline baseline and `tokendog floor` need each connector's schema size,
and those sizes are not on disk — the only way to get them is to start the
server and ask (`tokendog surface --refresh`). Expecting a person to remember to
run that is how the baseline stays blank forever. So this runs it FOR them, with
three safety rails, because starting servers is a real side effect:

  Only when needed.  Skipped unless the inventory is missing, older than a week,
                     or the set of enabled connectors has changed since the last
                     probe. A steady setup re-probes at most weekly.
  Never blocks.      The probe is spawned DETACHED and this hook returns at once.
                     The session never waits on a server that is slow or hanging;
                     a server that would prompt for credentials simply times out
                     in the background (the probe caps every connector at 20s) and
                     is recorded unreachable. Sizes appear on the NEXT session.
  Opt-out.           TOKENDOG_AUTO_REFRESH=0 (or off/no/false) disables it, and
                     TOKENDOG_QUIET mutes the one-line notice while still refreshing.

It writes only the inventory cache — no config is changed, nothing is disabled.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _bootstrap import ensure_tokendog
except Exception:  # pragma: no cover
    def ensure_tokendog() -> bool:
        return True

STALE_DAYS = 7.0          # re-probe a healthy inventory no more often than weekly
THROTTLE_HOURS = 12.0     # never launch a probe twice within this window


def _off(name: str, default: str = "") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in ("0", "false", "no", "off")


def _quiet() -> bool:
    return str(os.environ.get("TOKENDOG_QUIET", "")).strip().lower() in ("1", "true", "yes", "on")


def _connectors_signature() -> str:
    """A cheap fingerprint of the ENABLED connector set — changes when you add or
    remove one, which is exactly when the inventory needs rebuilding."""
    try:
        from tokendog.surface import read_enablement
        names = sorted(n for n, r in read_enablement()["connectors"].items()
                       if r.get("enabled"))
        return ",".join(names)
    except Exception:
        return ""


def _decide(home_dir: str) -> tuple[bool, str]:
    """(should_refresh, reason). Pure — touches only the cache/state files."""
    from tokendog.config import tokendog_home
    from tokendog.surface import load_inventory
    state_path = tokendog_home() / "autorefresh.json"
    now = time.time()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    # Throttle first: one launch per window regardless of the reason.
    last = float(state.get("last_attempt", 0) or 0)
    if now - last < THROTTLE_HOURS * 3600:
        return False, "throttled"
    inv = load_inventory().get("tools", {})
    sig = _connectors_signature()
    if not inv:
        return True, "no inventory yet"
    if sig and sig != state.get("connectors"):
        return True, "connector set changed"
    inv_path = tokendog_home() / "inventory.json"
    try:
        age_days = (now - inv_path.stat().st_mtime) / 86400.0
    except OSError:
        age_days = STALE_DAYS + 1
    if age_days >= STALE_DAYS:
        return True, f"inventory {age_days:.0f}d old"
    return False, "fresh"


def _mark_attempt() -> None:
    try:
        from tokendog.config import tokendog_home
        p = tokendog_home() / "autorefresh.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"last_attempt": time.time(),
                                 "connectors": _connectors_signature()}), encoding="utf-8")
    except Exception:
        pass


def _spawn_refresh() -> bool:
    """Launch `surface --refresh` detached, so it outlives this hook and never
    blocks the session. Returns True if it was launched."""
    try:
        from tokendog.config import tokendog_home
        log = open(tokendog_home() / "autorefresh.log", "a", encoding="utf-8")
    except Exception:
        log = subprocess.DEVNULL
    # The probe launches the MCP servers, most of which are node binaries. A hook
    # normally inherits Claude Code's PATH (so node is found), but a leaner
    # launch environment can miss it and every node server would fail to probe.
    # Prepend the common package-manager bin dirs as insurance; harmless if absent.
    env = dict(os.environ)
    extra = [d for d in ("/opt/homebrew/bin", "/usr/local/bin",
                         os.path.expanduser("~/.local/bin"))
             if os.path.isdir(d) and d not in env.get("PATH", "")]
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    try:
        subprocess.Popen(
            [sys.executable, "-m", "tokendog.report", "surface", "--refresh"],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True,   # detach from this hook's process group
            env=env,
        )
        return True
    except Exception:
        return False


def _write_floor_cache() -> None:
    """Write the always-on baseline sizes where the statusline can read them.

    The statusline runs under a bare `python3` that usually cannot import
    tokendog, so it cannot compute the baseline itself — it reads this cache.
    This hook IS tokendog-capable (via _bootstrap), so it writes it, every
    session, cheaply (config + disk, no transcript scan). Independent of the
    auto-refresh toggle: the baseline should show even when probing is off.
    """
    try:
        from tokendog.config import tokendog_home
        from tokendog.floor import floor_sizes
        p = tokendog_home() / "statusline_floor.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"sizes": floor_sizes()}), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    if not ensure_tokendog():
        return 0
    _write_floor_cache()                       # always — the statusline baseline
    if _off("TOKENDOG_AUTO_REFRESH", "1"):     # probing is opt-out; the cache is not
        return 0
    try:
        raw = sys.stdin.read()
        json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        pass
    try:
        should, reason = _decide(os.path.expanduser("~"))
        if not should:
            return 0
        _mark_attempt()
        if not _spawn_refresh():
            return 0
        if not _quiet():
            first = reason == "no inventory yet"
            msg = ("TokenDog: measuring MCP tool-schema sizes in the background "
                   + ("(first run — the statusline baseline will show connector sizes next "
                      "session)" if first else f"({reason})")
                   + ". No config is changed. Set TOKENDOG_AUTO_REFRESH=0 to turn this off.")
            print(json.dumps({"systemMessage": msg}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
