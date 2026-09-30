from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import learn_model
from .config import tokendog_home
from .learn import (
    MIN_SCORE,
    existing_for_gate,
    extract,
    gate,
    lessons,
    save_lesson,
    score,
    session_hash,
    signal_text,
    write_status,
)

# One capture run: mine the bytes added since last time, and keep what survives.
#
#   read new bytes → extract → prefilter → generate → gate → critic → save
#
# The offset only advances on an ANSWER. A failed model call is not "nothing to
# learn here": advancing past it would drop that part of the session for good,
# so it is left for the next PreCompact or SessionEnd to retry.

_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
LOCK_STALE_S = 600


def offset_path(session_id: str) -> Path:
    return tokendog_home() / "learn" / f"{session_hash(session_id)}.json"


def _enabled() -> bool:
    return str(os.environ.get("TOKENDOG_LEARN", "on")).strip().lower() not in (
        "0", "off", "false", "no")


def _read_new(transcript: Path, offset: int) -> tuple[list[dict], int]:
    """Records after `offset`, and the offset of the last complete line read."""
    try:
        size = transcript.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0                       # rewritten: not the file we read
    try:
        with transcript.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
    except OSError:
        return [], offset
    last_nl = chunk.rfind(b"\n")
    if last_nl == -1:
        return [], offset                # nothing complete yet
    body, new_offset = chunk[:last_nl + 1], offset + last_nl + 1
    records = []
    for line in body.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records, new_offset


def _load_offset(session_id: str) -> int:
    try:
        return int(json.loads(offset_path(session_id).read_text(encoding="utf-8"))["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _save_offset(session_id: str, offset: int) -> None:
    p = offset_path(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"offset": offset, "at": int(time.time())}), encoding="utf-8")


def _lock(session_id: str) -> Path | None:
    """An exclusive lock file, or None if another worker holds a fresh one."""
    lock = offset_path(session_id).with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            return lock
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime < LOCK_STALE_S:
                    return None
                lock.unlink()            # stale: a worker died holding it
            except OSError:
                return None
    return None


def _corroborate(repo: Path, file: str, sh: str) -> None:
    """Seeing your own lesson again in another session: count it."""
    path = repo / ".claude" / "learnings" / "_local" / file
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    m = re.search(r"^sessions: (\d+)$", text, re.M)
    seen = re.search(r"^seen_in: (\[.*\])$", text, re.M)
    try:
        seen_list = json.loads(seen.group(1)) if seen else []
    except ValueError:
        seen_list = []
    if sh in seen_list:
        return
    seen_list.append(sh)
    if m:
        text = text[:m.start(1)] + str(int(m.group(1)) + 1) + text[m.end(1):]
    if seen:
        text = re.sub(r"^seen_in: \[.*\]$", "seen_in: " + json.dumps(seen_list), text,
                      count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")


def capture(session_id: str, transcript, repo) -> dict:
    """Run one capture. Never raises; the result says what happened."""
    if not _enabled():
        return {"state": "off"}
    if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
        return {"state": "refused"}
    repo, transcript = Path(repo), Path(transcript)
    lock = _lock(session_id)
    if lock is None:
        return {"state": "locked"}
    try:
        return _capture(session_id, transcript, repo)
    except Exception as exc:       # a worker must never die leaving a lie on disk
        write_status(repo, state="failed", added=0)
        return {"state": "failed", "error": str(exc)[:200]}
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _capture(session_id: str, transcript: Path, repo: Path) -> dict:
    records, new_offset = _read_new(transcript, _load_offset(session_id))
    sig = extract(records)
    sc = score(sig)
    if sc < MIN_SCORE:
        _save_offset(session_id, new_offset)
        write_status(repo, state="skipped", added=0)
        return {"state": "skipped", "score": sc}

    write_status(repo, state="running")
    titles = [les["title"] for les in lessons(repo, "shared") + lessons(repo, "local")]
    candidates = learn_model.generate(signal_text(sig), titles=titles)
    if candidates is None:
        write_status(repo, state="failed", added=0)
        return {"state": "failed", "stage": "generate", "score": sc}

    gated = gate(candidates, repo=repo, existing=existing_for_gate(repo))
    sh = session_hash(session_id)
    for file in gated["corroborated"]:
        _corroborate(repo, file, sh)

    kept = learn_model.critic(gated["kept"])
    if kept is None:
        write_status(repo, state="failed", added=0)
        return {"state": "failed", "stage": "critic", "score": sc}

    for lesson in kept:
        save_lesson(repo, lesson, session_hash=sh)
    _save_offset(session_id, new_offset)
    write_status(repo, state="done", added=len(kept))
    return {"state": "done", "added": len(kept), "score": sc,
            "candidates": len(candidates), "gated": len(gated["kept"]),
            "corroborated": len(gated["corroborated"]), "rejected": gated["rejected"]}
