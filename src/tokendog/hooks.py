from __future__ import annotations

import subprocess
from pathlib import Path

from .outcomes import SESSION_TRAILER

# The commit stamp that turns outcome attribution from a heuristic into a fact.
#
# Every Claude Code shell call carries CLAUDE_CODE_SESSION_ID, and that id is
# exactly the transcript's sessionId — the key tokendog already uses for cost.
# So a `prepare-commit-msg` hook that copies it into a `Claude-Session:` trailer
# makes each commit name the precise session that produced it: no time-window or
# file-overlap guessing, and it is correct across squash, rebase and amend
# because it travels in the message, not in the timestamps.
#
# The hook is deliberately inert outside a Claude session — a hand commit gets
# no trailer and is correctly attributed to no session — and it never overwrites
# an existing trailer, so amending in a second session appends rather than
# clobbers. It shells out to `git interpret-trailers`, which owns the blank-line
# and dedup rules so the hook does not have to.

MARKER = "# tokendog:session-trailer"   # lets us recognise (and cleanly remove) our own hook

HOOK_BODY = f"""#!/bin/sh
{MARKER} — stamp the Claude Code session id so AI spend can be tied to what it shipped.
# Inert outside a Claude session (a hand commit gets no trailer). Never clobbers
# an existing trailer. Safe on merge/squash/amend. Remove with: tokendog install-hook --remove
[ -n "$CLAUDE_CODE_SESSION_ID" ] || exit 0
case "$2" in merge) exit 0 ;; esac   # don't touch a merge commit's canned message
git interpret-trailers --in-place --if-exists addIfDifferent \\
  --trailer "{SESSION_TRAILER}: $CLAUDE_CODE_SESSION_ID" "$1" 2>/dev/null || \\
  printf '\\n{SESSION_TRAILER}: %s\\n' "$CLAUDE_CODE_SESSION_ID" >> "$1"
exit 0
"""

HOOK_NAME = "prepare-commit-msg"


def _git_dir(repo: Path) -> Path | None:
    """The repo's git dir (handles worktrees, where .git is a file)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-dir"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    gd = Path(out.stdout.strip())
    return gd if gd.is_absolute() else (repo / gd)


def hook_status(repo=".") -> dict:
    """What, if anything, is installed at prepare-commit-msg in this repo."""
    repo_path = Path(repo).expanduser().resolve()
    gd = _git_dir(repo_path)
    if gd is None:
        return {"repo": str(repo_path), "is_repo": False, "installed": False,
                "path": None, "foreign": False}
    hook = gd / "hooks" / HOOK_NAME
    if not hook.exists():
        return {"repo": str(repo_path), "is_repo": True, "installed": False,
                "path": str(hook), "foreign": False}
    text = hook.read_text(encoding="utf-8", errors="replace")
    ours = MARKER in text
    return {"repo": str(repo_path), "is_repo": True, "installed": ours,
            "path": str(hook), "foreign": not ours}


def install(repo=".", *, force: bool = False) -> dict:
    """Install the session-trailer hook. Refuses to clobber a foreign hook.

    Reversible: `remove()` deletes exactly what this wrote and nothing else.
    """
    st = hook_status(repo)
    if not st["is_repo"]:
        return st | {"ok": False, "action": "none", "reason": "not a git repository"}
    hook = Path(st["path"])
    if st["foreign"] and not force:
        return st | {"ok": False, "action": "none",
                     "reason": "a different prepare-commit-msg hook is already here; "
                               "pass --force to append tokendog's, or install manually"}
    if st["foreign"] and force:
        # Append our block to the existing hook rather than destroy it.
        existing = hook.read_text(encoding="utf-8", errors="replace").rstrip("\n")
        body = existing + "\n\n" + "\n".join(HOOK_BODY.splitlines()[1:]) + "\n"
        hook.write_text(body, encoding="utf-8")
        hook.chmod(0o755)
        return st | {"ok": True, "action": "appended", "reason": None}
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK_BODY, encoding="utf-8")
    hook.chmod(0o755)
    return st | {"ok": True, "action": "installed" if not st["installed"] else "reinstalled",
                 "reason": None}


def remove(repo=".") -> dict:
    """Remove the hook we installed. Leaves a foreign hook untouched."""
    st = hook_status(repo)
    if not st["is_repo"] or not st["path"]:
        return st | {"ok": False, "action": "none", "reason": "not a git repository"}
    hook = Path(st["path"])
    if not hook.exists():
        return st | {"ok": False, "action": "none", "reason": "no hook installed"}
    text = hook.read_text(encoding="utf-8", errors="replace")
    if MARKER not in text:
        return st | {"ok": False, "action": "none",
                     "reason": "the hook here was not installed by tokendog; leaving it alone"}
    # If it is purely ours, delete it; if we appended to a foreign hook, strip only our block.
    lines = text.splitlines()
    if lines and lines[0].startswith("#!") and MARKER in (lines[1] if len(lines) > 1 else ""):
        hook.unlink()
        return st | {"ok": True, "action": "removed", "reason": None}
    kept, skipping = [], False
    for ln in lines:
        if MARKER in ln:
            skipping = True
            continue
        if skipping and ln.strip() == "exit 0":
            skipping = False
            continue
        if not skipping:
            kept.append(ln)
    hook.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")
    return st | {"ok": True, "action": "stripped", "reason": None}


def format_install(result: dict) -> str:
    if result.get("ok"):
        verb = {"installed": "Installed", "reinstalled": "Reinstalled",
                "appended": "Appended to the existing hook",
                "removed": "Removed", "stripped": "Stripped tokendog's block from the hook"
                }.get(result["action"], result["action"])
        lines = [f"{verb}: {result['path']}"]
        if result["action"] in ("installed", "reinstalled", "appended"):
            lines.append(f"Commits made from a Claude Code session now carry a "
                         f"`{SESSION_TRAILER}:` trailer, so `tokendog outcomes` can tie them to "
                         f"the exact session — no heuristic. Hand commits are unaffected.")
        return "\n".join(lines)
    return f"Not done: {result.get('reason', 'unknown')}\n  ({result.get('path') or result.get('repo')})"
