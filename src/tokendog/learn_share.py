from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .learn import SIMILAR, learn_dir, lessons, read_lesson, similar, write_status

# Share personal lessons with the repo, as one PR a code owner reviews.
#
# OFF unless TOKENDOG_LEARN_SHARE=on. It pushes a branch and opens a pull request
# on the user's repository — outward-facing, visible to their team, and not
# something a tool should start doing just because it was installed.
#
# When on:
#   - Due at most weekly, and only for a lesson at least a day old: the day is
#     the author's chance to forget a bad one before anyone else sees it.
#   - One PR per repo. An open learnings PR from anyone is appended to.
#   - Built in a throwaway `git worktree`. The user's branch, checkout and
#     uncommitted work are never touched.
#   - `git add -f` exactly the lesson files and INDEX.md; never `_local/`.
#   - Review is the one human gate, and it is the one that matters: a merged
#     lesson loads into every developer's sessions.

BRANCH_PREFIX = "tokendog-learnings/"
PERSONAL = ("seen_in", "created_at", "shared", "sessions")


def _hours(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, default))
        return default if v < 0 else v
    except (TypeError, ValueError):
        return default


def _config(repo: Path) -> dict:
    try:
        cfg = json.loads((learn_dir(repo) / "config.json").read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled(repo: Path, *, explicit: bool = False) -> bool:
    """Automatic sharing needs TOKENDOG_LEARN_SHARE=on. `explicit` is a person
    running `--share-now`, which is consent in a way installing a plugin is not.
    A repo's own `{"share": false}` blocks both."""
    if _config(repo).get("share", True) is False:
        return False
    if explicit:
        return True
    return str(os.environ.get("TOKENDOG_LEARN_SHARE", "off")).strip().lower() in ("on", "1", "true", "yes")


def _status(repo: Path) -> dict:
    try:
        return json.loads((learn_dir(repo) / "_local" / ".status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def unshared(repo: Path, *, min_age_h: float | None = None) -> list[dict]:
    age = _hours("TOKENDOG_LEARN_SHARE_MIN_AGE_H", 24) if min_age_h is None else min_age_h
    cutoff = (time.time() - age * 3600) * 1000
    out = []
    for les in lessons(repo, "local"):
        meta = les["meta"]
        if str(meta.get("shared", "no")) != "no":
            continue
        if float(meta.get("created_at") or 0) > cutoff:
            continue
        out.append(les)
    return out


def share_due(repo: Path) -> bool:
    if not enabled(repo):
        return False
    last = float(_status(repo).get("last_share_at") or 0) / 1000
    if time.time() - last < _hours("TOKENDOG_LEARN_SHARE_EVERY_H", 168) * 3600:
        return False
    return bool(unshared(repo))


# --- running git and gh -------------------------------------------------------


def _run(cmd, cwd, *, check=True) -> str:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if check and done.returncode != 0:
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''}: "
                           f"{(done.stderr or done.stdout).strip()[:200]}")
    return done.stdout.strip()


def gh(args: list[str], cwd) -> str:
    """The GitHub CLI. A seam, so tests fake GitHub and nothing else."""
    return _run(["gh", *args], cwd)


def _base(repo: Path) -> str:
    cfg = _config(repo).get("base")
    if isinstance(cfg, str) and re.match(r"^[A-Za-z0-9._/-]+$", cfg):
        return cfg
    head = _run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo, check=False)
    return head.split("/", 1)[1] if head.startswith("origin/") else "main"


def _email_hash(repo: Path) -> str:
    email = _run(["git", "config", "user.email"], repo, check=False) or "anon"
    return hashlib.sha256(email.encode()).hexdigest()[:6]


def _open_pr(repo: Path) -> dict | None:
    """An open learnings PR from anyone, to append to rather than open another."""
    try:
        prs = json.loads(gh(["pr", "list", "--state", "open", "--json",
                             "number,headRefName,url", "--limit", "100"], repo) or "[]")
    except (RuntimeError, ValueError, OSError):
        return None
    for pr in prs if isinstance(prs, list) else []:
        if str(pr.get("headRefName", "")).startswith(BRANCH_PREFIX):
            return pr
    return None


# --- writing the shared files -------------------------------------------------


def _shared_body(les: dict, developers: int) -> str:
    meta = {k: v for k, v in les["meta"].items() if k not in PERSONAL}
    meta["developers"] = developers
    src = les["path"].read_text(encoding="utf-8")
    end = src.find("\n---", 3)
    body = src[end + 4:] if src.startswith("---") and end != -1 else src
    head = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
    return f"---\n{head}\n---\n{body.lstrip(chr(10)) if body.startswith(chr(10)) else body}"


def _bump_developers(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^developers: (\d+)$", text, re.M)
    if m:
        text = text[:m.start(1)] + str(int(m.group(1)) + 1) + text[m.end(1):]
    else:
        text = text.replace("\n---\n", "\ndevelopers: 2\n---\n", 1)
    path.write_text(text, encoding="utf-8")


def build_index(learn: Path) -> str:
    rows = []
    for p in sorted(learn.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        les = read_lesson(p)
        if les:
            dev = les["meta"].get("developers", 1)
            rows.append(f"- [{les['title']}]({p.name})"
                        + (f" — corroborated by {dev} developers" if int(dev or 1) > 1 else ""))
    return "# Learnings\n\nLessons captured from sessions and reviewed here. Generated; " \
           "edit the lesson files, not this.\n\n" + "\n".join(rows) + "\n"


def share(repo, *, now: float | None = None, explicit: bool = False) -> dict:
    """Share unshared lessons. Never raises; never touches the user's checkout.

    `explicit` skips the day-old wait: the person asking is the reviewer the
    wait was protecting.
    """
    repo = Path(repo)
    if not enabled(repo, explicit=explicit):
        return {"state": "off"}
    todo = unshared(repo, min_age_h=0 if explicit else None)
    if not todo:
        return {"state": "nothing"}
    if not shutil.which("git"):
        return {"state": "failed", "error": "git not found"}
    wt = None
    try:
        try:
            gh(["auth", "status"], repo)
        except (RuntimeError, OSError) as exc:
            write_status(repo, share_state="failed", share_error="gh not signed in")
            return {"state": "failed", "error": f"gh: {exc}"[:200]}

        _run(["git", "fetch", "origin"], repo)
        base = _base(repo)
        pr = _open_pr(repo)
        if pr:
            branch = pr["headRefName"]
            start = f"origin/{branch}"
        else:
            day = datetime.fromtimestamp(now or time.time(), timezone.utc).strftime("%Y-%m-%d")
            branch, n = f"{BRANCH_PREFIX}{day}-{_email_hash(repo)}", 2
            while _run(["git", "ls-remote", "--heads", "origin", branch], repo, check=False):
                branch, n = f"{BRANCH_PREFIX}{day}-{_email_hash(repo)}-{n}", n + 1
            start = f"origin/{base}"

        wt = Path(tempfile.mkdtemp(prefix="tokendog-learn-"))
        _run(["git", "worktree", "add", "--detach", str(wt), start], repo)
        _run(["git", "checkout", "-B", branch], wt)

        learn = wt / ".claude" / "learnings"
        learn.mkdir(parents=True, exist_ok=True)
        existing = [read_lesson(p) for p in learn.glob("*.md") if p.name != "INDEX.md"]
        existing = [e for e in existing if e]
        written, corroborated = [], []
        for les in todo:
            match = next((e for e in existing if similar(les["title"], e["title"]) >= SIMILAR), None)
            if match:
                _bump_developers(match["path"])
                corroborated.append(match["title"])
                written.append(match["path"])
                continue
            dest = learn / les["file"]
            dest.write_text(_shared_body(les, 1), encoding="utf-8")
            written.append(dest)
        index = learn / "INDEX.md"
        index.write_text(build_index(learn), encoding="utf-8")

        # -f: many repos gitignore .claude/. Exactly these files, never _local.
        rel = [str(p.relative_to(wt)) for p in written + [index]]
        _run(["git", "add", "-f", "--", *rel], wt)
        prefix = str(_config(repo).get("commitPrefix") or "")
        title = f"{prefix}Session learnings: {len(todo)} lesson{'s' if len(todo) != 1 else ''}"
        _run(["git", "-c", "user.useConfigOnly=false", "commit", "--no-verify", "-m", title], wt)
        _run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], wt)

        body = "Lessons captured automatically from Claude Code sessions. Review each one: a " \
               "merged lesson loads into every developer's sessions in this repo.\n\n" + \
               "\n".join(f"- **{les['title']}**"
                         + (" (corroborated)" if les["title"] in corroborated else "")
                         + f"\n  {les['rule'][:300]}" for les in todo)
        if pr:
            url = pr["url"]
            gh(["pr", "comment", str(pr["number"]), "--body", body], repo)
        else:
            url = gh(["pr", "create", "--head", branch, "--base", base, "--title", title,
                      "--body", body], repo).splitlines()[-1]

        for les in todo:
            text = les["path"].read_text(encoding="utf-8")
            les["path"].write_text(re.sub(r'^shared: .*$', f"shared: {json.dumps(url)}",
                                          text, count=1, flags=re.M), encoding="utf-8")
        write_status(repo, share_state="done", last_share_at=int(time.time() * 1000),
                     share_url=url, shared=len(todo))
        return {"state": "done", "url": url, "shared": len(todo),
                "corroborated": len(corroborated), "branch": branch}
    except Exception as exc:
        write_status(repo, share_state="failed", share_error=str(exc)[:200])
        return {"state": "failed", "error": str(exc)[:200]}
    finally:
        if wt is not None:
            _run(["git", "worktree", "remove", "--force", str(wt)], repo, check=False)
            shutil.rmtree(wt, ignore_errors=True)
