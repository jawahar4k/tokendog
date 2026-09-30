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
LIMIT_WARN = 60.0       # % of a rate-limit window consumed
LIMIT_ALARM = 85.0

# --- ansi ---------------------------------------------------------------------
DIM = "\033[2m"
RED = "\033[31m"
YEL = "\033[33m"
GRN = "\033[32m"
CYA = "\033[36m"
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
        return f"{n / 1_000:.0f}K"
    return f"{n / 1_000_000:.2f}M"


def window_label(size: int | None) -> str:
    if not size:
        return "?"
    return "1M" if size >= 1_000_000 else f"{size // 1000}K"


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

    # A reader-friendly token count: keep one decimal under 10K so 2,335 reads as
    # "2.3K", not "2K" — the floor numbers are small and the precision matters.
    def fk(n: int) -> str:
        n = int(n or 0)
        if n < 1_000:
            return str(n)
        if n < 10_000:
            return f"{n / 1_000:.1f}K"
        return human(n)

    # Show EVERY kind that is present, spelled out, so "floor 2.4K (MCP 2.3K ·
    # skills 66)" reads at a glance. The full itemised list is `tokendog floor`.
    NAMES = [("mcp", "MCP"), ("skill", "skills"), ("instruction", "instructions")]
    parts = [f"{label} {fk(sizes[key])}" for key, label in NAMES if sizes.get(key)]
    breakdown = f" ({' · '.join(parts)})" if parts else ""
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


def split_segment(payload: dict) -> str | None:
    """`setup 70K (sys 26K · mcp 31K · skills 13K) · work 95K`.

    The two halves answer different questions: setup shrinks only by disabling
    something and is re-injected by /clear, work shrinks only by /clear. Showing
    them apart is the whole point — a single percentage cannot say which.
    """
    if str(os.environ.get("TOKENDOG_STATUSLINE_SPLIT", "1")).strip().lower() in (
            "0", "false", "no", "off"):
        return None
    split = read_split(payload)
    if not split:
        return None
    setup, work = split.get("setup") or {}, split.get("work") or {}
    setup_total, work_total = int(split.get("setup_total") or 0), int(split.get("work_total") or 0)
    if not setup_total and not work_total:
        return None
    # Parts under this are noise on a one-line statusline: knowing the subagent
    # listing costs 300 tokens changes nothing anyone would do.
    NAMED = (("sys", "sys"), ("mcp", "mcp"), ("skills", "skills"), ("agents", "agents"))
    parts = [f"{label} {human(setup[key])}" for key, label in NAMED
             if int(setup.get(key) or 0) >= 500]
    text = f"setup {human(setup_total)}"
    if parts:
        text += " (" + " · ".join(parts) + ")"
    text += f" · work {human(work_total)}"
    return paint(text, grade(float(setup_total), SETUP_ALARM, SETUP_ALARM * 2)
                 if setup_total >= SETUP_ALARM else DIM)


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


def _update_lookup(home: str) -> tuple[str, str] | None:
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
    return installed, available


def update_segment(*, home: str | None = None, use_cache: bool = True) -> str | None:
    """`v0.5.0→0.6.0 /plugin update`, only when the installed copy is behind."""
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
    if not pair or len(pair) != 2:
        return None
    installed, available = str(pair[0]), str(pair[1])
    # One of these came from a file a marketplace controls, and this prints to
    # a terminal: anything that is not plainly a version is refused.
    if not (_SAFE_VERSION.match(installed) and _SAFE_VERSION.match(available)):
        return None
    if version_newer(installed, available) is not True:
        return None
    return paint(f"v{installed}→{available} /plugin update", YEL)


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


def _split_seg(payload: dict) -> Seg | None:
    """The split, with its reductions: full → shrinkable-only → largest → total."""
    text = split_segment(payload)
    if not text:
        return None
    split = read_split(payload) or {}
    setup = split.get("setup") or {}
    setup_total = int(split.get("setup_total") or 0)
    work_total = int(split.get("work_total") or 0)
    colour = grade(float(setup_total), SETUP_ALARM, SETUP_ALARM * 2) if setup_total >= SETUP_ALARM else DIM
    # sys is not shrinkable by the reader; mcp/skills/agents are, by disabling.
    shrinkable = [(k, int(setup.get(k) or 0)) for k in ("mcp", "skills", "agents")
                  if int(setup.get(k) or 0) >= 500]
    red = []
    if shrinkable:
        red.append((30, paint(f"setup {human(setup_total)} ("
                              + " · ".join(f"{k} {human(v)}" for k, v in shrinkable)
                              + f") · work {human(work_total)}", colour)))
        big = max(shrinkable, key=lambda kv: kv[1])
        red.append((40, paint(f"setup {human(setup_total)} ({big[0]} {human(big[1])})"
                              f" · work {human(work_total)}", colour)))
    red.append((50, paint(f"setup {human(setup_total)} · work {human(work_total)}", colour)))
    red.append((70, None))
    return Seg(text, red)


def build(payload: dict) -> str:
    segs: list[Seg | None] = []

    where = where_segment(payload)
    if where:
        # Folder alone first; the branch is the first thing worth losing.
        folder_only = paint(_ANSI.sub("", where).split(" ⎇ ")[0], CYA)
        segs.append(Seg(where, [(20, folder_only), (60, None)]))

    model = dig(payload, "model", "display_name") or dig(payload, "model", "id")
    if model:
        label = str(model) + ("⚡" if payload.get("fast_mode") else "")
        segs.append(Seg(paint(label, BLD), [(25, None)]))

    # Context occupancy — the headline, and never dropped. Absolute tokens
    # first: that is the number that multiplies into every later call.
    used = occupancy(payload)
    size = num(dig(payload, "context_window", "context_window_size"))
    if used is not None:
        colour = grade(float(used), CTX_WARN, CTX_ALARM)
        base = f"ctx {human(used)}/{window_label(int(size) if size else None)}"
        pct = num(dig(payload, "context_window", "used_percentage"))
        band = band_of(used)
        full = base + (f" {pct:.0f}%" if pct is not None else "") + (f" [{band}]" if band else "")
        segs.append(Seg(paint(full, colour), [(35, paint(base, colour))]))

    age_ms = num(dig(payload, "cost", "total_duration_ms"))
    if age_ms and age_ms / 3_600_000 >= 1:
        hours = age_ms / 3_600_000
        segs.append(Seg(paint(f"age {hours:.1f}h", grade(hours, AGE_WARN_H, AGE_ALARM_H)),
                        [(15, None)]))

    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        seg = limit_segment(payload, key, label)
        if seg:
            segs.append(Seg(seg, [(45, None)]))

    # An org spend limit past 75% never drops: it is the one that ends the day.
    sp = spend_segment(payload)
    if sp:
        segs.append(Seg(sp, []))

    cs = cache_segment(payload)
    if cs:
        segs.append(Seg(cs, [(55, None)]))

    spl = _split_seg(payload)
    if spl:
        segs.append(spl)
    else:
        flr = floor_segment()
        if flr:
            segs.append(Seg(flr, [(10, None)]))

    sv = savings_segment(payload)
    if sv:
        segs.append(Seg(sv, [(12, None)]))

    spend = num(dig(payload, "cost", "total_cost_usd"))
    if spend:
        segs.append(Seg(paint(cost_text(spend), DIM), [(18, None)]))

    up = update_segment()
    if up:
        segs.append(Seg(up, [(8, None)]))

    # One actionable verb, and only when something is actually wrong — a nudge
    # that fires constantly is a nudge nobody reads. The split knows which half
    # is heavy, so it names the lever that works; occupancy is the fallback.
    hint = lever_hint(payload)
    if not hint:
        if used is not None and used >= CTX_ALARM:
            hint = "→ /clear"
        elif used is not None and used >= CTX_WARN:
            hint = "→ /compact"
        elif age_ms and age_ms / 3_600_000 >= AGE_ALARM_H:
            hint = "→ stale, /clear"
    if hint:
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
