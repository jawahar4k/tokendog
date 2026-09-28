from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Stop a large whole-file read BEFORE it enters the window, rather than cutting
# it afterwards. Measured on real transcripts, files the model asked to read are
# 98% of the large-payload weight, and cutting their middles is exactly the thing
# the condenser refuses to do. So the only honest lever left is not to pull the
# whole file in the first place — read a range, grep it, or let a subagent read
# it in ITS window and hand back a summary.
#
# THE COST RULE, which is the whole feature:
#
#   Blocking costs one extra round trip. That round trip re-reads the entire
#   window at cache-read rate, so it costs 0.1·ctx.
#
#   Keeping a file of F tokens costs a cache write (2× input for the 1h TTL)
#   plus a cache read on each of the ~20 turns that follow:
#       2·F + 0.1·F·20 = 4·F
#
#   So blocking pays exactly while:   0.1·ctx < 4·F   ⇔   ctx < 40·F
#
# A 6k-token file is therefore worth guarding below ~240k of context and never
# above it. This is what makes the guard a measurement product rather than a
# nag: it charges the intervention its own cost, and declines to act when the
# intervention loses. Nothing else in TokenDog does that yet.

# Blocking pays while ctx < RATIO · file_tokens. 40 comes from the 1h-TTL cache
# write (2×) plus ~20 remaining turns at cache-read rate (0.1× each).
DEFAULT_RATIO = 40

# Below this a file is not worth an extra round trip whatever the context.
MIN_TOKENS = 6_000

# Measured on real transcripts: code and prose run about 2.1 characters per
# token, not the 4 a naive count assumes. The ledger measures this per session;
# here there is only a file on disk, so the measured constant is the best we get.
CHARS_PER_TOKEN = 2.1

# Reading an 800 MB file to decide whether it is big is its own denial of
# service. Past this, "large" without counting.
HUGE_BYTES = 8 * 1024 * 1024

# Never a text read worth guarding, and several are binary that would be sniffed
# anyway — cheaper to skip by name.
SKIP_EXT = frozenset((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
                      ".pdf", ".ipynb", ".svg", ".zip", ".gz", ".tar", ".woff",
                      ".woff2", ".ttf", ".mp4", ".mov", ".mp3", ".wasm", ".so",
                      ".dylib", ".class", ".jar", ".pyc"))

SNIFF_BYTES = 8192


@dataclass
class Decision:
    action: str            # "allow" | "suggest"
    reason: str
    file_tokens: int = 0
    context_tokens: int = 0
    ext: str = ""

    def as_record(self) -> dict:
        """What the ledger stores. Numeric plus an extension — never the path.

        A path leaks the home directory and often a client's name, which is the
        same line every other TokenDog surface holds.
        """
        return {"action": self.action, "file_tokens": self.file_tokens,
                "context_tokens": self.context_tokens, "ext": self.ext}


def _env_num(name: str, default: float) -> float:
    """Unset, non-numeric or negative means default; 0 stays a valid value."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return default if value < 0 else value


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def mode() -> str:
    """`off` | `shadow` (default) | `enforce`.

    Shadow is the default for every content-altering feature here, and the
    guard is no exception: it records what it would have done so the decision
    can be judged on the reader's own history before it ever changes one.
    """
    value = str(os.environ.get("TOKENDOG_READGUARD_MODE", "shadow")).strip().lower()
    if _truthy(os.environ.get("TOKENDOG_OBSERVE_ONLY")):
        return "shadow"
    return value if value in ("off", "shadow", "enforce") else "shadow"


def block_pays(*, file_tokens: int, context_tokens: int | None) -> bool:
    """Is one extra round trip cheaper than carrying this file? See the header.

    An unknown or zero context means the rule cannot be evaluated. It returns
    False there on purpose: a guess that blocks is worse than no guard at all.
    """
    if not context_tokens or file_tokens <= 0:
        return False
    return context_tokens < _env_num("TOKENDOG_READGUARD_RATIO", DEFAULT_RATIO) * file_tokens


def is_whole_file_read(tool_input: dict) -> bool:
    """A Read with no range. A ranged read is already what we would ask for."""
    if not isinstance(tool_input, dict) or not tool_input.get("file_path"):
        return False
    return tool_input.get("offset") in (None, "") and tool_input.get("limit") in (None, "")


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\0" in fh.read(SNIFF_BYTES)
    except OSError:
        return True


def estimate_tokens(path) -> int:
    """Roughly what this file would add to the window, without tokenising it."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return 0
    if size >= HUGE_BYTES:
        return int(size / CHARS_PER_TOKEN)
    return int(size / CHARS_PER_TOKEN)


_SUGGESTION = (
    "This is a {tokens:,}-token whole-file read at {ctx:,} of context. Everything it "
    "adds is re-read on every later turn, so it is cheaper here to: Grep for what you "
    "need and Read with offset/limit; or read the first ~60 lines as an outline; or "
    "delegate to the bulk-reader subagent, whose read stays in its own window and "
    "returns a summary with file:line references. Reading it again allows it."
)


def assess(tool_input: dict, *, context_tokens: int | None, seen=None) -> Decision:
    """What to do about one `Read`. Never raises — a hook calls this.

    `seen` is the set of paths already assessed in this session; a repeat is
    always allowed. This is a speed bump, not a wall: someone who reads a file
    after being asked not to meant it, and refusing twice just buys another
    round trip to say no.
    """
    if mode() == "off":
        return Decision("allow", "guard off")
    if not is_whole_file_read(tool_input):
        return Decision("allow", "ranged read")

    raw_path = tool_input.get("file_path")
    path = Path(str(raw_path))
    ext = path.suffix.lower()
    if ext in SKIP_EXT:
        return Decision("allow", "not a text read", ext=ext)
    try:
        if not path.is_file():
            return Decision("allow", "not a file", ext=ext)
    except OSError:
        return Decision("allow", "unreadable", ext=ext)

    key = str(path)
    if seen is not None and key in seen:
        return Decision("allow", "already suggested for this file", ext=ext)

    tokens = estimate_tokens(path)
    if tokens < MIN_TOKENS:
        return Decision("allow", "small enough to carry", tokens, context_tokens or 0, ext)
    if _looks_binary(path):
        return Decision("allow", "binary", tokens, context_tokens or 0, ext)
    if not block_pays(file_tokens=tokens, context_tokens=context_tokens):
        return Decision("allow",
                        "cheaper to read it than to spend a round trip avoiding it",
                        tokens, context_tokens or 0, ext)

    if seen is not None:
        seen.add(key)
    return Decision("suggest",
                    _SUGGESTION.format(tokens=tokens, ctx=context_tokens or 0),
                    tokens, context_tokens or 0, ext)
