#!/usr/bin/env python3
"""TokenDog statusline — makes context economics visible while you work.

Claude Code pipes a JSON payload to this script on every interaction and renders
whatever it prints. See https://code.claude.com/docs/en/statusline

Why this exists: cost per call scales with how full the context window is, because
the whole prompt is re-billed every turn. That number was invisible during the
session that motivated this script — 30 calls at ~980K occupancy cost 29.5M tokens
to add 27K of content. The window occupancy and the 7-day limit are the two figures
that would have prevented it, so they go on screen permanently.

Thresholds are absolute token counts, not percentages of the window. A 1M window at
30% is 300K carried on every turn — expensive in absolute terms even though the
percentage looks calm. Measured on this machine's own history: turns at or above
200K carried 83.5% of all context tokens, which is where CTX_WARN comes from.

Every field is treated as optional: the payload shape varies by Claude Code version
and by whether a session has made an API call yet. A statusline that raises renders
as an error on every keystroke, so this one degrades to fewer segments instead.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

# The brand mark, shown before the figures so the line is identifiable at a
# glance among other statusline output. Override with TOKENDOG_STATUSLINE_TAG
# ("TokenDog" for a text mark, empty to drop it) — some terminals render an
# emoji at double width or not at all, and that is the reader's call, not ours.
TAG = os.environ.get("TOKENDOG_STATUSLINE_TAG", "\U0001F415")

# --- thresholds ---------------------------------------------------------------
CTX_WARN = 200_000      # absolute occupancy where per-turn cost starts to bite
CTX_ALARM = 400_000     # past here a trivial tool call costs more than most replies
AGE_WARN_H = 8.0        # a session older than a workday has drifted from its task
AGE_ALARM_H = 24.0      # overnight-idle sessions resume at full occupancy
# Keyed on HISTORY, deliberately, not on how full the window looks. A percentage
# cannot tell a connector you never call from a transcript you could clear, and
# /clear re-injects setup — so a percentage rule nags at a 1M window that is
# fine and stays silent on a 120k one that is all dead schema.
WORK_ALARM = 400_000    # history this big is what /clear and /compact are for
SETUP_ALARM = 120_000   # re-sent every turn; only disabling something moves it
# A loaded session left idle — the hygiene report's "Close it now". Restated
# because this script cannot import tokendog; a test pins them to the report's.
IDLE_STALE_H = 12.0         # untouched this long and it is not being come back to today
STALE_MIN_CONTEXT = 150_000  # ...and still holding this much, so reopening it is expensive
LIMIT_WARN = 60.0       # % of a rate-limit window consumed
LIMIT_ALARM = 85.0

# --- ansi ---------------------------------------------------------------------
DIM = "\033[2m"
RED = "\033[31m"
YEL = "\033[33m"
GRN = "\033[32m"
CYA = "\033[36m"
MAG = "\033[35m"
BLD = "\033[1m"
OFF = "\033[0m"


def paint(text: str, colour: str) -> str:
    return f"{colour}{text}{OFF}" if colour else text


def grade(value: float | None, warn: float, alarm: float) -> str:
    """Colour for a value where higher is worse. Unknown values stay uncoloured."""
    if value is None:
        return ""
    if value >= alarm:
        return RED
    if value >= warn:
        return YEL
    return GRN


def human(n: float | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    if n < 1_000:
        return f"{n:.0f}"
    if n < 1_000_000:
        return f"{n / 1_000:.0f}k"
    return f"{n / 1_000_000:.2f}M"


def window_label(size: int | None) -> str:
    if not size:
        return "?"
    return "1M" if size >= 1_000_000 else f"{size // 1000}k"


def dig(obj, *path, default=None):
    """Walk nested dict keys, returning `default` the moment anything is missing."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def num(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def occupancy(payload: dict) -> int | None:
    """Tokens carried into the current turn.

    Prefers the harness's own total; falls back to summing the per-turn usage
    parts. Output tokens are deliberately excluded — they came back, they were
    not carried, and including them would overstate what the next turn re-reads.
    """
    total = num(dig(payload, "context_window", "total_input_tokens"))
    if total:
        return int(total)
    usage = dig(payload, "context_window", "current_usage", default={})
    if isinstance(usage, dict):
        parts = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        summed = sum(num(usage.get(k)) or 0 for k in parts)
        if summed:
            return int(summed)
    return None


def floor_segment() -> str | None:
    """The always-on floor (MCP schemas + skills + instructions), by size.

    ON by default; set TOKENDOG_STATUSLINE_FLOOR=0 (or off/no) to hide it. It is
    the "baseline" — the tokens loaded into the window before you type anything:
    every enabled MCP's tool schemas, each skill's always-on header, the
    instruction files. A fixed, static cost paid on every turn. It is shown apart
    from the ctx figure on purpose: ctx is dominated by conversation history, and
    Claude Code's payload carries NO per-source breakdown of that, so the
    baseline is the one honest source split the statusline can show.

    Pure READ, no tokendog import: the statusline runs under whatever `python3`
    is first on PATH (often /usr/bin/python3, which cannot import tokendog), so
    it must not depend on the import. A tokendog-capable SessionStart hook writes
    the sizes to a small JSON cache; this just reads and renders it. If the cache
    is missing, the segment is dropped and the line still renders — the
    statusline's works-without-tokendog guarantee is preserved.
    """
    # On by default; only an explicit off-value hides it.
    if str(os.environ.get("TOKENDOG_STATUSLINE_FLOOR", "1")).strip().lower() in (
            "0", "false", "no", "off"):
        return None
    home = os.environ.get("TOKENDOG_HOME") or os.path.join(os.path.expanduser("~"), ".tokendog")
    cache_path = os.path.join(home, "statusline_floor.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            sizes = json.load(fh).get("sizes")
    except (OSError, ValueError):
        return None
    total = sizes.get("total") if isinstance(sizes, dict) else None
    if not total:
        return None

    # Same format as the measured setup segment — lowercase k, parts in brackets
    # separated by commas — with one decimal under 10k, since these are small.
    def fk(n: int) -> str:
        n = int(n or 0)
        if n < 1_000:
            return str(n)
        if n < 10_000:
            return f"{n / 1_000:.1f}k"
        return human(n)

    # "baseline", not "setup": this is what is installed on disk, measured before
    # the session's own split exists, and it cannot see the system prompt.
    NAMES = [("mcp", "mcp"), ("skill", "skills"), ("instruction", "instructions")]
    parts = [f"{label} {fk(sizes[key])}" for key, label in NAMES if sizes.get(key)]
    breakdown = f" ({', '.join(parts)})" if parts else ""
    return paint(f"baseline {fk(total)}{breakdown}", DIM)


def read_split(payload: dict) -> dict | None:
    """This session's setup/work split, as the Stop hook last left it.

    Pure READ, no tokendog import: the statusline runs under whatever `python3`
    is first on PATH (often /usr/bin/python3, which cannot import tokendog), so
    it must not depend on the import. `split_refresh` writes the file at the end
    of each turn; this just reads it. Missing or corrupt → None, and the line
    renders without the segment.
    """
    session = payload.get("session_id")
    if not isinstance(session, str) or not re.match(r"^[A-Za-z0-9-]{8,64}$", session):
        return None
    home = os.environ.get("TOKENDOG_HOME") or os.path.join(os.path.expanduser("~"), ".tokendog")
    try:
        with open(os.path.join(home, "split", session + ".json"), encoding="utf-8") as fh:
            split = json.load(fh)["split"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return split if isinstance(split, dict) else None


def _setup_parts(setup: dict, keys=("sys", "mcp", "skills", "agents")) -> list[str]:
    # Parts under 500 tokens are noise on a one-line statusline.
    return [f"{k} {human(setup[k])}" for k in keys if int(setup.get(k) or 0) >= 500]


def _setup_text(total: int, parts: list[str]) -> str:
    """`setup 75k` plus the parts, dimmed, in brackets and separated by commas.

    Commas, not ` · `: the dot separates segments, and reusing it inside one
    reads as four segments where there is one.
    """
    # Setup survives /clear; past these lines only disabling something helps.
    colour = RED if total >= SETUP_ALARM * 2 else YEL if total >= SETUP_ALARM else MAG
    head = paint(f"setup {human(total)}", colour)
    return head + (paint(" (" + ", ".join(parts) + ")", DIM) if parts else "")


def split_segment(payload: dict) -> str | None:
    """`setup 75k (sys 34k, mcp 21k, skills 14k, agents 6k)`.

    Setup is what a percentage cannot show: the part of the window re-sent on
    every turn that only disabling something shrinks, and `/clear` re-injects.
    The work half still decides the `/clear` hint; printed, it did not change
    what anyone did, so it is not.
    """
    if str(os.environ.get("TOKENDOG_STATUSLINE_SPLIT", "1")).strip().lower() in (
            "0", "false", "no", "off"):
        return None
    split = read_split(payload)
    if not split:
        return None
    setup = split.get("setup") or {}
    total = int(split.get("setup_total") or 0)
    if not total:
        return None
    return _setup_text(total, _setup_parts(setup))


def lever_hint(payload: dict) -> str | None:
    """One verb, aimed at the half that is actually heavy.

    History is what /clear and /compact remove. Setup survives them both, so a
    session carrying 300k of connector schema is told to look at the floor, not
    to throw away its working state for nothing.
    """
    split = read_split(payload)
    if not split:
        return None
    if int(split.get("work_total") or 0) >= WORK_ALARM:
        return "→ /clear or /compact"
    if int(split.get("setup_total") or 0) >= SETUP_ALARM:
        return "→ setup is heavy, /tokendog:floor"
    return None


def savings_segment(payload: dict) -> str | None:
    """What the condenser has saved THIS session, when it is switched on.

    ON by default; set TOKENDOG_STATUSLINE_SAVINGS=0 (or off/no) to hide it. Like
    the baseline, it is a pure cache READ (no tokendog import): a tokendog-capable
    writer records each condense event and keeps a small per-session total in
    `${TOKENDOG_HOME or ~/.tokendog}/statusline_savings.json`; this looks up the
    current session and renders it. Absent until the first event — which only
    happens once the condenser is in shadow/enforce mode — so the segment stays
    off, and off the line, for anyone who has not turned condensing on.

    This is the RECORDED saving (what actually happened), never the projection —
    the projection is a multi-second transcript scan and has no place on a line
    that redraws on every keystroke; it lives in the dashboard Savings tab.
    """
    if str(os.environ.get("TOKENDOG_STATUSLINE_SAVINGS", "1")).strip().lower() in (
            "0", "false", "no", "off"):
        return None
    sid = payload.get("session_id")
    if not sid:
        return None
    home = os.environ.get("TOKENDOG_HOME") or os.path.join(os.path.expanduser("~"), ".tokendog")
    try:
        with open(os.path.join(home, "statusline_savings.json"), encoding="utf-8") as fh:
            sessions = json.load(fh).get("sessions") or {}
    except (OSError, ValueError):
        return None
    entry = sessions.get(sid) if isinstance(sessions, dict) else None
    saved = (entry or {}).get("saved") if isinstance(entry, dict) else None
    if not saved:
        return None
    return paint(f"saved {human(saved)}", GRN)


def limit_segment(payload: dict, key: str, label: str) -> str | None:
    pct = num(dig(payload, "rate_limits", key, "used_percentage"))
    if pct is None:
        return None
    return paint(f"{label} {pct:.0f}%", grade(pct, LIMIT_WARN, LIMIT_ALARM))


# --- where: folder and branch -------------------------------------------------

# A branch name is text from a file anyone with write access to the repo can
# edit, and this line goes straight to a terminal. Anything outside this set is
# refused rather than escaped: a refused name is a missing segment, a
# mis-escaped one is a terminal someone else is driving.
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/+@-]{1,120}$")


def _git_dir(start: str) -> str | None:
    """The git directory for `start`, walking up; following a worktree pointer.

    In a normal checkout `.git` is a directory. In a worktree it is a FILE that
    says `gitdir: <path>`, relative to the worktree when not absolute.
    """
    d = os.path.abspath(start)
    for _ in range(64):
        cand = os.path.join(d, ".git")
        if os.path.isdir(cand):
            return cand
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8", errors="replace") as fh:
                    line = fh.readline().strip()
            except OSError:
                return None
            if line.startswith("gitdir:"):
                target = line[len("gitdir:"):].strip()
                return target if os.path.isabs(target) else os.path.normpath(os.path.join(d, target))
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def git_branch(start: str | None) -> str | None:
    """The current branch, or a short SHA when detached — by reading one file.

    No `git` subprocess: this runs on every keystroke, and in a large repo a
    subprocess per render is a visible lag.
    """
    if not start:
        return None
    gd = _git_dir(start)
    if not gd:
        return None
    try:
        with open(os.path.join(gd, "HEAD"), encoding="utf-8", errors="replace") as fh:
            head = fh.read(512).strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[len("ref:"):].strip()
        name = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        return name if _SAFE_REF.match(name) else None
    return head[:7] if re.match(r"^[0-9a-f]{7,64}$", head) else None


def where_segment(payload: dict) -> str | None:
    """`acme-api ⎇ main` — the folder's name, never its path, and the branch."""
    cwd = dig(payload, "workspace", "current_dir")
    if not isinstance(cwd, str) or not cwd:
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    if not cwd:
        return None
    # Basename only: the full path leaks the home directory and, as often as
    # not, a client's name — the same line every other surface here holds.
    folder = os.path.basename(cwd.rstrip("/")) or cwd
    if not _SAFE_REF.match(folder):
        folder = re.sub(r"[^A-Za-z0-9._@+-]", "?", folder)[:60]
    branch = dig(payload, "worktree", "branch")
    if not (isinstance(branch, str) and _SAFE_REF.match(branch)):
        branch = git_branch(cwd)
    text = folder + (f" ⎇ {branch}" if branch else "")
    return paint(text, CYA)


# --- cache ---------------------------------------------------------------------

CACHE_REBUILT_WINDOW_S = 600     # a miss older than this is not news
CACHE_REBUILT_MIN = 50_000       # a small rebuild is not worth a segment
CACHE_EXPIRY_WARN_S = 600        # count down only in the last ten minutes


def _epoch_s(value) -> float | None:
    """Timestamps arrive in ms; accept seconds too rather than guess wrong."""
    n = num(value)
    if n is None or n <= 0:
        return None
    return n / 1000.0 if n > 1e11 else n


def cache_segment(payload: dict, *, now: float | None = None) -> str | None:
    """Silent when healthy. Speaks only when the cache is about to cost something.

    A segment that is always present is a segment nobody reads, and this one
    matters in exactly three moments: the next turn will rewrite the prefix, the
    last turn just did, or the prefix is about to expire.
    """
    pc = payload.get("prompt_cache")
    if not isinstance(pc, dict):
        return None
    now = time.time() if now is None else now
    if pc.get("warm") is False:
        cold = num(pc.get("recache_tokens_if_cold"))
        if cold:
            return paint(f"cache cold: next turn re-caches {human(cold)}", YEL)
        return paint("cache cold", YEL)
    missed = _epoch_s(pc.get("last_miss_at"))
    rebuilt = num(pc.get("miss_recache_tokens"))
    if missed and rebuilt and rebuilt >= CACHE_REBUILT_MIN and 0 <= now - missed < CACHE_REBUILT_WINDOW_S:
        return paint(f"cache rebuilt {human(rebuilt)}", YEL)
    expires = _epoch_s(pc.get("expires_at"))
    if expires and 0 < expires - now < CACHE_EXPIRY_WARN_S:
        return paint(f"cache expires {max(1, round((expires - now) / 60))}m", DIM)
    return None


# --- spend limit ---------------------------------------------------------------

SPEND_WARN, SPEND_ALARM = 75.0, 90.0


def spend_segment(payload: dict) -> str | None:
    """An org spend limit, when one is set and it is getting close."""
    pct = num(dig(payload, "rate_limits", "spend_limit", "used_percentage"))
    if pct is None or pct < SPEND_WARN:
        return None
    return paint(f"spend {pct:.0f}%", RED if pct >= SPEND_ALARM else YEL)


# --- context bar ---------------------------------------------------------------

BAR_CELLS = 6
BAR_WARN, BAR_ALARM = 50.0, 80.0


_SEVERITY = {GRN: 0, YEL: 1, RED: 2}


def ctx_colour(pct: float | None, used: float | None) -> str:
    """The worse of two readings: how full the window is, and what it bills.

    Percentage alone lies on a big window: 365k is re-read on every turn
    whether the window is 200k or 1M, but on 1M it reads 37% and green. The
    absolute lines are the ones the cost actually crosses.
    """
    by_pct = GRN if pct is None or pct < BAR_WARN else YEL if pct < BAR_ALARM else RED
    by_abs = GRN if used is None or used < CTX_WARN else YEL if used < CTX_ALARM else RED
    return max(by_pct, by_abs, key=lambda c: _SEVERITY[c])


def ctx_bar(pct: float, used: float | None = None) -> str:
    """`▓▓░░░░` — how full the window is, coloured by the worse of fullness
    and absolute tokens carried."""
    pct = max(0.0, min(100.0, float(pct)))
    filled = min(BAR_CELLS, round(pct / 100 * BAR_CELLS))
    colour = ctx_colour(pct, used)
    return paint("▓" * filled, colour) + paint("░" * (BAR_CELLS - filled), DIM)


# --- lessons -------------------------------------------------------------------


def _ago(ms) -> str | None:
    at = _epoch_s(ms)
    if not at:
        return None
    secs = max(0, time.time() - at)
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86_400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86_400)}d ago"


def lessons_segment(payload: dict) -> str | None:
    """What session learning did last, in this repo. Hidden where it never ran.

    A failed capture is shown, not hidden: one that fails silently looks
    exactly like one that found nothing.
    """
    cwd = dig(payload, "workspace", "current_dir") or payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    st = _read_json(os.path.join(cwd, ".claude", "learnings", "_local", ".status.json"))
    if not isinstance(st, dict):
        return None
    ago = _ago(st.get("at"))
    when = f" ({ago})" if ago else ""
    state = st.get("state")
    if state == "running":
        return paint("learning…", DIM)
    if state == "failed":
        return paint(f"lesson capture failed{when}", YEL)
    added = int(num(st.get("added")) or 0)
    if added > 0:
        return paint(f"learned {added} lesson{'s' if added != 1 else ''}{when}", GRN)
    return paint(f"no new lessons{when}", DIM)


# --- band ----------------------------------------------------------------------

# Restated from tokendog.bands, because this script cannot import tokendog. A
# test pins the two tuples equal, so the statusline and `tokendog bands` agree
# by construction rather than by someone remembering to update both.
BANDS = (
    ("<50k", 0, 50_000),
    ("50-100k", 50_000, 100_000),
    ("100-150k", 100_000, 150_000),
    ("150-200k", 150_000, 200_000),
    ("200-400k", 200_000, 400_000),
    ("400k+", 400_000, None),
)


def band_of(ctx: float | None) -> str | None:
    if ctx is None:
        return None
    for label, lower, upper in BANDS:
        if ctx >= lower and (upper is None or ctx < upper):
            return label
    return None


# --- cost ----------------------------------------------------------------------


def cost_text(usd: float) -> str:
    """`≈$2.14`, or `≈$1234` once cents stop mattering.

    `≈` because this is the client's list-price estimate, not an invoice — on a
    subscription there is no per-token charge at all.
    """
    return f"≈${usd:.0f}" if usd >= 99.995 else f"≈${usd:.2f}"


# --- update notice -------------------------------------------------------------
#
# `claude plugin update` compares the installed version with the marketplace's,
# and the installed copy is a snapshot. Three times in this project's life the
# installed plugin sat behind its own source with nothing saying so. This reads
# two local files — no network, nothing fetched — and speaks only when behind.

PLUGIN = "tokendog"
UPDATE_CACHE_S = 60          # read two JSON files at most once a minute, not per key
_SAFE_VERSION = re.compile(r"^[0-9][0-9A-Za-z.-]{0,29}$")


def _semver(v: str):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    return tuple(int(x) for x in m.groups()) if m else None


def version_newer(installed: str, available: str) -> bool | None:
    """True if `available` is newer. None when the two cannot be compared.

    Only like with like: both semver, or both plain integers (the claude.ai org
    directory versions as "0017"). Anything else returns None — no notice beats
    a confident wrong one.
    """
    a, b = _semver(installed), _semver(available)
    if a and b:
        return b > a
    if installed.isdigit() and available.isdigit():
        return int(available) > int(installed)
    return None


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _update_lookup(home: str) -> tuple[str, str, str] | None:
    plugins = os.path.join(home, ".claude", "plugins")
    inst = _read_json(os.path.join(plugins, "installed_plugins.json")) or {}
    records = [r for key, rs in (inst.get("plugins") or {}).items()
               if isinstance(key, str) and key.startswith(PLUGIN + "@") and isinstance(rs, list)
               for r in rs if isinstance(r, dict)]
    if not records:
        return None
    # Most recently updated, not highest version: the two version schemes do
    # not sort against each other, and the newest record is the one in use.
    rec = max(records, key=lambda r: str(r.get("lastUpdated") or ""))
    installed = str(rec.get("version") or "")
    market = str(next(k for k in (inst.get("plugins") or {})
                      if k.startswith(PLUGIN + "@"))).split("@", 1)[1]
    known = (_read_json(os.path.join(plugins, "known_marketplaces.json")) or {}).get(market) or {}
    root = known.get("installLocation") or (known.get("source") or {}).get("path")
    if not root:
        return None
    manifest = _read_json(os.path.join(root, ".claude-plugin", "marketplace.json")) or {}
    entry = next((p for p in manifest.get("plugins") or []
                  if isinstance(p, dict) and p.get("name") == PLUGIN), None)
    available = str((entry or {}).get("version") or "")
    return installed, available, str(rec.get("lastUpdated") or "")


def _iso_epoch(value: str) -> float | None:
    from datetime import datetime
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


RELOAD_SCAN_BYTES = 8 * 1024 * 1024


def last_reload(transcript: str | None) -> float | None:
    """When `/reload-plugins` last ran in this session, from the transcript tail.

    Matches only the harness's own record of it — a `system` / `local_command`
    whose output starts `Reloaded:` — never text that merely mentions the
    command, which an assistant reply can easily do. Reads the tail only, and
    only when a restart notice would otherwise be shown.
    """
    if not isinstance(transcript, str) or not transcript:
        return None
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            fh.seek(max(0, size - RELOAD_SCAN_BYTES))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    latest = None
    for line in tail.splitlines():
        if "local_command" not in line or "Reloaded:" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (rec.get("type") == "system" and rec.get("subtype") == "local_command"
                and str(rec.get("content", "")).startswith("<local-command-stdout>Reloaded:")):
            at = _iso_epoch(str(rec.get("timestamp") or ""))
            if at and (latest is None or at > latest):
                latest = at
    return latest


def update_segment(*, home: str | None = None, use_cache: bool = True,
                   session_age_s: float | None = None,
                   transcript: str | None = None) -> str | None:
    """One of two notices, or nothing:

      `v0.10.1→0.10.2 /plugin update`       the installed copy is behind its source
      `0.10.2 installed, /reload-plugins`    it is current, but THIS window loaded
                                             its hooks before the update, and has
                                             not reloaded since

    The second is the one that matters day to day: updating a plugin does not
    reach a session that is already open, and nothing else on screen says so.
    """
    home = home or os.path.expanduser("~")
    state = os.environ.get("TOKENDOG_HOME") or os.path.join(os.path.expanduser("~"), ".tokendog")
    cache = os.path.join(state, "statusline_update.json")
    pair = None
    if use_cache:
        hit = _read_json(cache)
        if isinstance(hit, dict) and time.time() - float(hit.get("at") or 0) < UPDATE_CACHE_S:
            pair = hit.get("pair")
    if pair is None:
        pair = _update_lookup(home)
        if use_cache:
            try:
                os.makedirs(state, exist_ok=True)
                with open(cache + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump({"at": time.time(), "pair": pair}, fh)
                os.replace(cache + ".tmp", cache)
            except OSError:
                pass
    if not pair or len(pair) not in (2, 3):
        return None
    installed, available = str(pair[0]), str(pair[1])
    updated_at = str(pair[2]) if len(pair) == 3 else ""
    # One of these came from a file a marketplace controls, and this prints to
    # a terminal: anything that is not plainly a version is refused.
    if not (_SAFE_VERSION.match(installed) and _SAFE_VERSION.match(available)):
        return None
    if version_newer(installed, available) is True:
        return paint(f"v{installed}→{available} /plugin update", YEL)
    installed_at = _iso_epoch(updated_at)
    if session_age_s and installed_at and installed_at > time.time() - session_age_s:
        # `/reload-plugins` loads it without a restart, so the session's start
        # time never moves; the reload itself is the evidence it is loaded.
        reloaded = last_reload(transcript)
        if reloaded and reloaded >= installed_at:
            return None
        return paint(f"{installed} installed, /reload-plugins to load it", YEL)
    return None


# --- width fitting -------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Seg:
    """A segment and the ways it can get shorter.

    `reductions` are (rank, shorter) pairs: `shorter` replaces the text, None
    drops the segment. Lower rank goes first. A segment with no reductions is
    never shortened and never dropped.
    """

    __slots__ = ("text", "reductions")

    def __init__(self, text: str, reductions=()):
        self.text = text
        self.reductions = sorted(reductions, key=lambda r: r[0])


def visible_len(s: str) -> int:
    return len(_ANSI.sub("", s))


def fit(segs, cols: int) -> str:
    """Join, then shorten in ONE global order — least useful first — to fit.

    Global matters: trimming whatever happens to be rightmost drops the verb at
    the end of the line before the trivia in the middle. `cols` 0 means the
    width is unknown, and an unknown width reduces nothing.
    """
    sep = paint(" · ", DIM)
    live = [s for s in segs if s and s.text]

    def joined():
        return sep.join(s.text for s in live)

    if cols <= 0:
        return joined()
    budget = max(10, cols - 4)
    while visible_len(joined()) > budget:
        best = None
        for s in live:
            if s.reductions and (best is None or s.reductions[0][0] < best.reductions[0][0]):
                best = s
        if best is None:
            break
        _rank, shorter = best.reductions.pop(0)
        if shorter is None:
            live.remove(best)
        else:
            best.text = shorter
    return joined()


def terminal_cols() -> int:
    """The terminal's width, or 0 when it cannot be known.

    A statusline has no tty of its own, so the width is found on an ancestor:
    walk up to four parent processes for a tty, then ask it. Env overrides win.
    """
    for name in ("TOKENDOG_STATUSLINE_COLS", "COLUMNS"):
        raw = os.environ.get(name)
        if raw:
            try:
                n = int(raw)
                if n > 0:
                    return n
            except ValueError:
                pass
    try:
        import subprocess
        pid = os.getppid()
        flag = "-f" if sys.platform == "darwin" else "-F"
        for _ in range(4):
            if pid <= 1:
                break
            out = subprocess.run(["ps", "-o", "tty=,ppid=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=0.5).stdout.split()
            if len(out) < 2:
                break
            tty, ppid = out[0], out[1]
            if tty not in ("?", "??", "-"):
                size = subprocess.run(["stty", flag, f"/dev/{tty}", "size"],
                                      capture_output=True, text=True, timeout=0.5).stdout.split()
                if len(size) == 2 and size[1].isdigit():
                    return int(size[1])
                break
            pid = int(ppid)
    except Exception:
        return 0
    return 0


def idle_hours(payload: dict) -> float | None:
    """Hours since the transcript was last written.

    On resume the statusline renders before the first new turn lands, so the
    transcript's mtime is when the session was left. That is idleness; session
    age is not — thirty hours of steady work is not a session left behind.
    """
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        return max(0.0, (time.time() - os.path.getmtime(path)) / 3600)
    except OSError:
        return None


def is_stale(payload: dict, used: float | None) -> bool:
    idle = idle_hours(payload)
    return (idle is not None and idle >= IDLE_STALE_H
            and used is not None and used >= STALE_MIN_CONTEXT)


def _split_seg(payload: dict) -> Seg | None:
    """Setup, with its reductions: every part → the shrinkable ones → the
    largest → just the total → gone. `sys` goes first because the reader
    cannot shrink it; the parts they can act on are the last to drop."""
    text = split_segment(payload)
    if not text:
        return None
    split = read_split(payload) or {}
    setup = split.get("setup") or {}
    total = int(split.get("setup_total") or 0)
    red = []
    shrinkable = _setup_parts(setup, ("mcp", "skills", "agents"))
    if shrinkable:
        red.append((30, _setup_text(total, shrinkable)))
        big = max(("mcp", "skills", "agents"), key=lambda k: int(setup.get(k) or 0))
        red.append((40, _setup_text(total, _setup_parts(setup, (big,)))))
    red.append((50, _setup_text(total, [])))
    red.append((70, None))
    return Seg(text, red)


def _ctx_seg(payload: dict, used: int | None) -> Seg | None:
    """`ctx 365k/1M ▓▓░░░░ 37%`, and `/compact` from 80% full."""
    if used is None:
        return None
    size = num(dig(payload, "context_window", "context_window_size"))
    pct = num(dig(payload, "context_window", "used_percentage"))
    if pct is None and size:
        pct = used / size * 100
    base = paint(f"ctx {human(used)}/{window_label(int(size) if size else None)}", BLD)
    if pct is None:
        return Seg(base, [])
    full = f"{base} {ctx_bar(pct, used)} {pct:.0f}%"
    if pct >= BAR_ALARM:
        full += " " + paint("/compact", RED)
    return Seg(full, [(35, f"{base} {pct:.0f}%" + (" " + paint("/compact", RED) if pct >= BAR_ALARM else ""))])


def build(payload: dict) -> str:
    """mark · folder ⎇ branch · model · ctx ▓▓░░ % · setup (…) · cache · lessons · ≈$"""
    segs: list[Seg | None] = []

    # Where first: across several windows it is what tells them apart.
    where = where_segment(payload)
    if where:
        folder_only = paint(_ANSI.sub("", where).split(" ⎇ ")[0], CYA)
        segs.append(Seg(where, [(20, folder_only), (60, None)]))

    model = dig(payload, "model", "display_name") or dig(payload, "model", "id")
    if model:
        label = str(model) + ("⚡" if payload.get("fast_mode") else "")
        segs.append(Seg(paint(label, BLD), [(25, None)]))

    used = occupancy(payload)
    segs.append(_ctx_seg(payload, used))

    # An org spend limit past 75% never drops: it is the one that ends the day.
    sp = spend_segment(payload)
    if sp:
        segs.append(Seg(sp, []))

    spl = _split_seg(payload)
    if spl:
        segs.append(spl)
    else:
        flr = floor_segment()
        if flr:
            segs.append(Seg(flr, [(10, None)]))

    cs = cache_segment(payload)
    if cs:
        segs.append(Seg(cs, [(55, None)]))

    ls = lessons_segment(payload)
    if ls:
        segs.append(Seg(ls, [(15, None)]))

    sv = savings_segment(payload)
    if sv:
        segs.append(Seg(sv, [(12, None)]))

    spend = num(dig(payload, "cost", "total_cost_usd"))
    if spend:
        segs.append(Seg(paint(cost_text(spend), DIM), [(18, None)]))

    age_ms = num(dig(payload, "cost", "total_duration_ms"))
    up = update_segment(session_age_s=age_ms / 1000 if age_ms else None,
                        transcript=payload.get("transcript_path"))
    if up:
        segs.append(Seg(up, [(8, None)]))

    # One verb, only when the history is what is heavy: /clear and /compact
    # remove history and nothing else. The 80% prompt already sits on ctx.
    # Stale outranks everything: a loaded session left idle is the cheapest fix
    # there is and the most expensive to ignore, and /clear is the whole fix.
    hint = "→ stale, /clear" if is_stale(payload, used) else lever_hint(payload)
    # No split cached yet — the first turns of a session, which is exactly when
    # a resumed window most needs saying. Absolute occupancy decides instead.
    if hint is None and read_split(payload) is None and used is not None:
        if used >= CTX_ALARM:
            hint = "→ /clear"
        elif used >= CTX_WARN:
            hint = "→ /compact"
    pct = num(dig(payload, "context_window", "used_percentage"))
    if hint and not (pct is not None and pct >= BAR_ALARM and hint.endswith("/compact")):
        segs.append(Seg(paint(hint, RED if "clear" in hint else YEL), []))

    line = fit([s for s in segs if s], terminal_cols())
    if not line:
        line = paint("no session data", DIM)
    # An ASCII mark is dimmed so it recedes; an emoji carries its own colour and
    # is left alone (ANSI dim on an emoji is ignored or muddies it).
    if not TAG:
        return line
    mark = TAG if not TAG.isascii() else paint(TAG, DIM)
    return f"{mark} {line}"


def main() -> int:
    # Never throws, always prints something. A statusline that errors prints a
    # traceback on every keystroke; the bare mark is the floor.
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    try:
        print(build(payload))
    except Exception:
        try:
            print(build({}))
        except Exception:
            print(TAG or "tokendog")
    return 0


if __name__ == "__main__":
    sys.exit(main())
