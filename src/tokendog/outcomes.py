from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .transcripts import response_key, transcript_root

# Outcome attribution: what did a session actually SHIP, and what did that cost?
#
# Every other report in tokendog answers "where did the tokens go". This one
# answers the question a manager asks instead — "was it worth it": dollars of AI
# spend per merged PR, per branch, per session that produced code. It is the one
# tuneloop feature tokendog did not have, and it is a JOIN, not a new
# measurement — the cost is already known per session; what was missing was the
# link from a session to the thing it produced.
#
# THE CHAIN, and where each link's confidence comes from:
#   session -> files    the Edit/Write/MultiEdit tool calls name the files a
#                       session changed. Exact — it is in the transcript.
#   files+time -> commit  a commit is attributed to a session when it lands in
#                       that session's active window (first turn .. last turn +
#                       a grace period) in the SAME repo AND its files overlap
#                       what the session touched. Time alone is too loose (two
#                       sessions can overlap); files alone miss a commit that
#                       touched extra files; together they are defensible.
#   commit -> PR        a merge commit's subject carries the PR number
#                       ("Merge pull request #123", "(#123)"). Git-only, so no
#                       network and no `gh` auth — a commit with no discoverable
#                       PR is grouped under its branch or left as "unattributed
#                       commits", never dropped.
#
# WHAT IS DELIBERATELY NOT CLAIMED. This is a HEURISTIC link, and it says so: a
# session can touch a file it never commits (explored and reverted), and a PR
# can gather work from several sessions (and a session feed several PRs). The
# cost attributed to a PR is therefore the sum of the sessions that plausibly
# fed it, apportioned by nothing finer than "did it contribute" — a fractional
# split would invent a precision the data does not carry. The report states the
# apportionment so the reader weights it accordingly.

GRACE_MINUTES = 20.0     # a commit lands shortly AFTER the turn that wrote the file
FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

# PR number in a merge-commit subject: GitHub's "Merge pull request #N", GitLab's
# "See merge request !N", and the squash-merge "(#N)" suffix.
import re

_PR_PATTERNS = (
    re.compile(r"Merge pull request #(\d+)"),
    re.compile(r"\(#(\d+)\)"),
    re.compile(r"See merge request [^!]*!(\d+)"),
)


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        out = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


@dataclass
class SessionFacts:
    """One session's window, the files it changed, and where it ran."""

    session: str
    cwd: str | None
    project: str | None
    first: datetime | None
    last: datetime | None
    turns: int = 0
    files: set = field(default_factory=set)   # repo-relative-ish paths, as written by the tool

    @property
    def repo(self) -> str | None:
        return self.cwd


def session_facts(transcript_root_path=None, *, project=None) -> list[SessionFacts]:
    """Walk transcripts once and collect per-session facts for attribution.

    Reads the raw transcripts rather than the event stream because it needs two
    things the privacy-filtered stream drops on purpose: the FULL `cwd` (to find
    the git repo) and the file paths from tool calls (to match commits). Those
    never leave this machine — the report prints basenames.
    """
    base = Path(transcript_root_path).expanduser() if transcript_root_path else transcript_root()
    if not base.exists():
        return []
    per: dict[str, SessionFacts] = {}
    for path in base.rglob("*.jsonl"):
        seen: set = set()
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                sid = rec.get("sessionId") or path.stem
                cwd = rec.get("cwd")
                fact = per.get(sid)
                if fact is None:
                    fact = per[sid] = SessionFacts(session=sid, cwd=None, project=None,
                                                   first=None, last=None)
                if isinstance(cwd, str) and cwd.strip() and not fact.cwd:
                    fact.cwd = cwd.strip().rstrip("/")
                    fact.project = Path(fact.cwd).name
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                ts = _parse(rec.get("timestamp"))
                if ts is not None:
                    fact.first = ts if fact.first is None else min(fact.first, ts)
                    fact.last = ts if fact.last is None else max(fact.last, ts)
                key = response_key(rec) or rec.get("uuid")
                if key and key not in seen and msg.get("usage"):
                    seen.add(key)
                    fact.turns += 1
                for block in (msg.get("content") or []):
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") not in FILE_TOOLS:
                        continue
                    inp = block.get("input") or {}
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if isinstance(fp, str) and fp.strip():
                        fact.files.add(fp.strip())
    facts = [f for f in per.values() if f.first is not None]
    if project:
        facts = [f for f in facts if f.project == project]
    return facts


# The commit trailer that makes attribution EXACT rather than heuristic. Written
# by the prepare-commit-msg hook (`tokendog install-hook`) from the
# CLAUDE_CODE_SESSION_ID env var, which every Claude Code shell call carries and
# which equals the transcript's own sessionId — so a commit names the exact
# session that produced it, no time-window or file-overlap guessing.
SESSION_TRAILER = "Claude-Session"


@dataclass
class Commit:
    sha: str
    when: datetime
    author: str
    subject: str
    files: set
    pr: int | None
    session: str | None = None    # from the Claude-Session trailer, when present


def git_author(repo: str) -> str | None:
    """This clone's configured commit email — the person working on this machine."""
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(repo).expanduser()), "config", "user.email"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    email = out.stdout.strip()
    return email or None


def git_commits(repo: str, since: datetime, until: datetime,
                *, author: str | None = None) -> list[Commit]:
    """Commits in `repo` between `since` and `until`, with their changed files.

    `author` restricts to one commit email. This is the guard against the
    shared-repo problem: a local clone's history contains commits fetched from
    everyone, and another person's commit that happens to land in a local
    session's window on a same-named file would otherwise be mis-attributed to
    that session. Filtering to the machine owner's email means a session is only
    ever linked to a commit THIS person actually made. It cannot fix the deeper
    limit — that this machine only sees its own AI sessions — but it stops the
    false positives that limit would otherwise produce.

    Never raises: a path that is not a git repo, or a machine without git,
    returns []. The caller treats "no commits" and "not a repo" the same — the
    session simply has no attributable outcome.
    """
    repo_path = Path(repo).expanduser()
    if not (repo_path / ".git").exists() and not (repo_path.exists()):
        return []
    # Fields, US-separated: sha, committer-date, author, subject, session trailer.
    # The trailer is the exact-attribution key; empty when the commit was not
    # made from a Claude session (a hand edit) — correctly leaving it unlinked.
    fmt = (f"%x1e%H%x1f%cI%x1f%an%x1f%s%x1f"
           f"%(trailers:key={SESSION_TRAILER},valueonly,separator=%x2C)")
    cmd = ["git", "-C", str(repo_path), "log",
           f"--since={since.isoformat()}", f"--until={until.isoformat()}",
           "--name-only", f"--pretty=format:{fmt}"]
    if author:
        cmd.append(f"--author={author}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    commits: list[Commit] = []
    for chunk in out.stdout.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        head, _, rest = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 4:
            continue
        sha, ciso, author, subject = parts[0], parts[1], parts[2], parts[3]
        session = (parts[4].strip() if len(parts) > 4 else "") or None
        if session and "," in session:      # a commit amended across sessions: take the last
            session = session.split(",")[-1].strip()
        when = _parse(ciso)
        if when is None:
            continue
        files = {ln.strip() for ln in rest.splitlines() if ln.strip()}
        pr = None
        for pat in _PR_PATTERNS:
            m = pat.search(subject)
            if m:
                pr = int(m.group(1))
                break
        commits.append(Commit(sha=sha, when=when, author=author,
                              subject=subject, files=files, pr=pr, session=session))
    return commits


def resolve_pr_via_gh(repo: str, sha: str) -> int | None:
    """The merged PR a commit belongs to, via `gh` — for repos whose history has
    no local merge commit (squash-and-merge on GitHub leaves none).

    GitHub API, not the model API: it costs GitHub rate limit, never Claude
    tokens. Opt-in (`--gh`) and fully guarded — no `gh`, not authed, not a
    GitHub remote, or offline all return None, and the commit falls back to its
    branch bucket. Called only for commits already linked to a session, so the
    number of calls is bounded by real outcomes, not by history size.
    """
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "-R", _remote_slug(repo) or "", "--state", "merged",
             "--search", sha, "--json", "number", "--limit", "1"],
            capture_output=True, text=True, timeout=20, check=False,
            cwd=str(Path(repo).expanduser()))
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        rows = json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    return int(rows[0]["number"]) if rows else None


def _remote_slug(repo: str) -> str | None:
    """owner/name for the origin remote, so `gh -R` targets the right repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(repo).expanduser()), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    url = out.stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _basename_overlap(session_files: set, commit_files: set) -> int:
    """How many of a commit's files the session also touched, matched on basename.

    Basename, not full path: the tool records an absolute or cwd-relative path
    and git records a repo-relative one, so a full-string compare would never
    match. Basename collisions (two `index.ts`) are possible but rare enough that
    a false match costs a little misattributed cost, never a wrong headline.
    """
    sb = {Path(f).name for f in session_files}
    cb = {Path(f).name for f in commit_files}
    return len(sb & cb)


def attribute(facts: list[SessionFacts], *, grace_minutes: float = GRACE_MINUTES,
              costs: dict | None = None, use_gh: bool = False,
              own_author_only: bool = True) -> dict:
    """Link sessions to commits to PRs, in each session's own repo.

    `costs` maps session id -> est_cost_usd (from `cost_summary`); absent, cost
    columns are simply zero and the report still shows the outcome structure.
    """
    costs = costs or {}
    by_repo: dict[str, list[SessionFacts]] = defaultdict(list)
    for f in facts:
        if f.repo:
            by_repo[f.repo].append(f)

    # commit sha -> {commit, sessions:set, method}
    commit_links: dict[str, dict] = {}
    unmatched_sessions: list[SessionFacts] = []

    for repo, repo_facts in by_repo.items():
        starts = [f.first for f in repo_facts if f.first]
        ends = [f.last for f in repo_facts if f.last]
        if not starts:
            continue
        author = git_author(repo) if own_author_only else None
        commits = git_commits(repo, min(starts) - timedelta(minutes=grace_minutes),
                              max(ends) + timedelta(minutes=grace_minutes),
                              author=author)
        if not commits:
            unmatched_sessions.extend(repo_facts)
            continue
        known = {f.session for f in repo_facts}
        matched: set = set()   # sessions that got at least one commit

        # PASS 1 — EXACT: a commit that carries a Claude-Session trailer names
        # its session outright. No time or file guessing; this is the whole
        # point of the trailer. A trailer for a session not on this machine is
        # ignored (we only attribute our own).
        heuristic_commits = []
        for c in commits:
            if c.session and c.session in known:
                link = commit_links.setdefault(
                    c.sha, {"commit": c, "sessions": set(), "overlap": 0, "method": "exact"})
                link["sessions"].add(c.session)
                link["method"] = "exact"
                matched.add(c.session)
            elif not c.session:
                heuristic_commits.append(c)
            # a trailer naming an unknown session: leave it for no-one

        # PASS 2 — HEURISTIC: only for commits with NO trailer (hand commits, or
        # made before the hook was installed). In-window + file overlap, as
        # before. A repo that stamps every commit never reaches this path.
        for f in repo_facts:
            if f.first is None or f.last is None:
                continue
            lo, hi = f.first - timedelta(minutes=grace_minutes), f.last + timedelta(minutes=grace_minutes)
            for c in heuristic_commits:
                if not (lo <= c.when <= hi):
                    continue
                overlap = _basename_overlap(f.files, c.files)
                if overlap > 0 or not f.files:
                    link = commit_links.setdefault(
                        c.sha, {"commit": c, "sessions": set(), "overlap": 0, "method": "heuristic"})
                    link["sessions"].add(f.session)
                    link["overlap"] = max(link["overlap"], overlap)
                    matched.add(f.session)

        unmatched_sessions.extend(f for f in repo_facts if f.session not in matched)

    # Fill in PR numbers the merge subject did not carry, via gh — but only for
    # commits that actually matched a session, so the network cost is bounded by
    # outcomes, not by history. Cached per sha within this call.
    if use_gh:
        repo_of_session = {f.session: f.repo for f in facts}
        gh_cache: dict[str, int | None] = {}
        for sha, link in commit_links.items():
            c = link["commit"]
            if c.pr is not None:
                continue
            repo = next((repo_of_session[s] for s in link["sessions"]
                         if repo_of_session.get(s)), None)
            if not repo:
                continue
            if sha not in gh_cache:
                gh_cache[sha] = resolve_pr_via_gh(repo, sha)
            if gh_cache[sha] is not None:
                c.pr = gh_cache[sha]

    # Group commits under their PR (or "no PR" bucket per repo/branch).
    pr_groups: dict = defaultdict(lambda: {"pr": None, "commits": [], "sessions": set(),
                                           "subjects": [], "repo": None, "methods": set()})
    for sha, link in commit_links.items():
        c = link["commit"]
        repo_name = None
        # find the repo this commit came from via its sessions
        for f in facts:
            if f.session in link["sessions"]:
                repo_name = f.project
                break
        gkey = f"pr:{c.pr}" if c.pr is not None else f"nopr:{repo_name}"
        g = pr_groups[gkey]
        g["pr"] = c.pr
        g["repo"] = repo_name
        g["commits"].append(c.sha[:8])
        g["subjects"].append(c.subject)
        g["sessions"].update(link["sessions"])
        g["methods"].add(link.get("method", "heuristic"))

    fact_by_session = {f.session: f for f in facts}
    out_groups = []
    for gkey, g in pr_groups.items():
        sess = sorted(g["sessions"])
        cost = round(sum(costs.get(s, 0.0) for s in sess), 4)
        turns = sum(fact_by_session[s].turns for s in sess if s in fact_by_session)
        # A group's confidence is its weakest link: all-exact only when every
        # contributing commit named its session.
        method = ("exact" if g["methods"] == {"exact"}
                  else "heuristic" if g["methods"] == {"heuristic"} else "mixed")
        out_groups.append({
            "pr": g["pr"],
            "repo": g["repo"],
            "commits": g["commits"],
            "commit_count": len(g["commits"]),
            "subject": g["subjects"][0] if g["subjects"] else "",
            "sessions": sess,
            "session_count": len(sess),
            "turns": turns,
            "est_cost_usd": cost,
            "shared": len(sess) > 1,   # cost is shared across sessions — see module note
            "method": method,
        })
    # PRs first (real outcomes), then no-PR buckets; each by cost.
    out_groups.sort(key=lambda g: (g["pr"] is None, -g["est_cost_usd"]))

    unmatched = sorted({f.session for f in unmatched_sessions})
    unmatched_cost = round(sum(costs.get(s, 0.0) for s in unmatched), 4)
    merged = [g for g in out_groups if g["pr"] is not None]
    exact_links = sum(1 for l in commit_links.values() if l.get("method") == "exact")
    return {
        "groups": out_groups,
        "totals": {
            "prs": len(merged),
            "attributed_cost": round(sum(g["est_cost_usd"] for g in merged), 4),
            "cost_per_pr": round(sum(g["est_cost_usd"] for g in merged) / len(merged), 4) if merged else None,
            "sessions_with_outcome": len({s for g in out_groups for s in g["sessions"]}),
            "unmatched_sessions": len(unmatched),
            "unmatched_cost": unmatched_cost,
            "exact_commits": exact_links,
            "heuristic_commits": len(commit_links) - exact_links,
        },
        "grace_minutes": grace_minutes,
    }


def outcomes_data(transcript_root_path=None, *, project=None, window=None,
                  use_gh: bool = False, own_author_only: bool = True) -> dict:
    """The outcomes report: cost per PR, from transcripts + git + cost_summary."""
    from .report import cost_summary
    facts = session_facts(transcript_root_path, project=project)
    if window is not None:
        # Keep a session if any of its life fell in the window — its commits are
        # dated near its turns, so the window scopes which sessions can own an
        # outcome without needing the turns themselves.
        facts = [f for f in facts
                 if (f.last and window.contains(f.last))
                 or (f.first and window.contains(f.first))]
    costs = {}
    try:
        rollup = cost_summary(group_by="session_id", project=project)
        costs = {r["key"]: r.get("est_cost_usd", 0.0) for r in rollup["rows"]}
    except Exception:
        costs = {}
    data = attribute(facts, costs=costs, use_gh=use_gh,
                     own_author_only=own_author_only)
    data["project"] = project
    data["window"] = window.label if window is not None else None
    data["own_author_only"] = own_author_only
    return data
