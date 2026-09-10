from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .outcomes import session_facts

# Attribute Claude spend to Glitch PIPELINES, not just the project it ran in.
#
# Glitch already does the hard half. Every pipeline run writes
# `<project>/.glitch/runs/<run_id>.json`, and each stage in it records the
# `session_id` Glitch handed to `claude --session-id` — which is therefore the
# exact name of the transcript Claude wrote, the same key tokendog costs by. So
# the join needs NO change to Glitch: read the run files, map
# session_id -> (pipeline, run, stage), and fold in the per-session cost tokendog
# already computes. A glitch pipeline stops being an anonymous `sdk-cli` session
# in `contextflow` and becomes "the implement stage of chunk-embed-store, run of
# 09-09 07:29, $X".
#
# WHY READ RUN FILES rather than the firmware DB: the firmware `context_log` is
# Glitch's own memory-tier estimate and was empty here; the run files carry the
# authoritative session_id + Glitch's own cost/token figures per stage, and they
# exist for every run.

RUN_ID_STAMP = re.compile(r"-\d{8}-\d+$")   # trailing -YYYYMMDD-<ms>


def _pipeline_name(run: dict, run_id: str) -> str:
    name = run.get("name")
    if isinstance(name, str) and name.strip():
        return RUN_ID_STAMP.sub("", name.strip()) or name.strip()
    return RUN_ID_STAMP.sub("", run_id) or run_id


def read_run_file(path: Path) -> list[dict]:
    """One row per stage that ran Claude, from a Glitch run-state file."""
    try:
        run = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(run, dict):
        return []
    run_id = run.get("run_id") or Path(path).stem
    pipeline = _pipeline_name(run, run_id)
    project = Path((run.get("project_dir") or "").rstrip("/")).name or None
    stages = run.get("stages")
    # `stages` is a dict keyed by stage name (the value's own `name` is often
    # null, so the KEY is the source of truth for the stage name).
    items = stages.items() if isinstance(stages, dict) else \
        [((s or {}).get("name"), s) for s in (stages or [])]
    out = []
    for stage_name, st in items:
        if not isinstance(st, dict):
            continue
        sid = st.get("session_id")
        if not sid:
            continue
        out.append({
            "session_id": sid,
            "pipeline": pipeline,
            "run_id": run_id,
            "stage": stage_name or "?",
            "project": project,
            "status": st.get("status"),
            # Glitch's OWN figures for this stage, kept for comparison with
            # tokendog's transcript-derived numbers.
            "glitch_cost_usd": st.get("cost_usd") or 0.0,
            "glitch_tokens_in": st.get("tokens_input") or 0,
            "glitch_tokens_out": st.get("tokens_output") or 0,
            "started_at": st.get("started_at") or run.get("started_at"),
        })
    return out


def discover_run_dirs(transcript_root_path=None, *, extra_roots=None) -> list[Path]:
    """The `.glitch/runs` directories worth reading: one per project that has
    Claude sessions (from the transcripts), plus any explicitly given roots.

    Derived from the sessions' own cwds so it reads exactly the repos in play,
    rather than scanning the whole disk.
    """
    dirs: set[Path] = set()
    for f in session_facts(transcript_root_path):
        if f.cwd:
            d = Path(f.cwd) / ".glitch" / "runs"
            if d.is_dir():
                dirs.add(d)
    for r in (extra_roots or []):
        d = Path(r).expanduser()
        rd = d / ".glitch" / "runs" if (d / ".glitch" / "runs").is_dir() else d
        if rd.is_dir():
            dirs.add(rd)
    return sorted(dirs)


def stage_rows(transcript_root_path=None, *, extra_roots=None) -> list[dict]:
    rows = []
    for d in discover_run_dirs(transcript_root_path, extra_roots=extra_roots):
        for f in sorted(d.glob("*.json")):
            rows.extend(read_run_file(f))
    return rows


def pipeline_costs(transcript_root_path=None, *, project=None, window=None,
                   extra_roots=None) -> dict:
    """Cost per Glitch pipeline (and per run), joining run files to session cost.

    tokendog's own per-session figures are the authority for spend (they come
    from the transcript's metered usage); Glitch's per-stage cost is shown
    alongside for a sanity check. A stage whose session tokendog never saw (a
    non-Claude provider, or a transcript since deleted) is still listed, with
    tokendog spend zero and Glitch's own figure shown.
    """
    from .report import cost_summary

    rows = stage_rows(transcript_root_path, extra_roots=extra_roots)
    # session_id -> tokendog cost/tokens
    costs: dict[str, dict] = {}
    try:
        for r in cost_summary(group_by="session_id")["rows"]:
            costs[r["key"]] = r
    except Exception:
        costs = {}

    # window/project scope by the session's own facts (last activity + project)
    fact = {f.session: f for f in session_facts(transcript_root_path)}

    by_pipeline: dict[str, dict] = defaultdict(
        lambda: {"pipeline": None, "runs": set(), "sessions": set(), "stages": 0,
                 "est_cost_usd": 0.0, "input": 0, "output": 0,
                 "glitch_cost_usd": 0.0, "projects": set()})
    seen_session_pipeline: set = set()
    for row in rows:
        sid = row["session_id"]
        f = fact.get(sid)
        if project and (not f or f.project != project):
            continue
        if window is not None:
            when = (f.last if f else None) or (f.first if f else None)
            if not window.contains(when):
                continue
        g = by_pipeline[row["pipeline"]]
        g["pipeline"] = row["pipeline"]
        g["runs"].add(row["run_id"])
        g["sessions"].add(sid)
        g["stages"] += 1
        g["glitch_cost_usd"] += float(row["glitch_cost_usd"] or 0)
        if row["project"]:
            g["projects"].add(row["project"])
        # tokendog cost is per SESSION; add it once per (session,pipeline) so a
        # multi-stage session is not double counted within its pipeline.
        c = costs.get(sid)
        if c and (sid, row["pipeline"]) not in seen_session_pipeline:
            seen_session_pipeline.add((sid, row["pipeline"]))
            g["est_cost_usd"] += float(c.get("est_cost_usd") or 0)
            g["input"] += int(c.get("input_tokens") or 0) + int(c.get("cache_read_tokens") or 0) \
                + int(c.get("cache_creation_tokens") or 0)
            g["output"] += int(c.get("output_tokens") or 0)

    groups = []
    for name, g in by_pipeline.items():
        groups.append({
            "pipeline": name,
            "runs": len(g["runs"]),
            "sessions": len(g["sessions"]),
            "stages": g["stages"],
            "input": g["input"],
            "output": g["output"],
            "est_cost_usd": round(g["est_cost_usd"], 4),
            "glitch_cost_usd": round(g["glitch_cost_usd"], 4),
            "projects": sorted(p for p in g["projects"] if p),
        })
    groups.sort(key=lambda r: -r["est_cost_usd"])
    return {
        "pipelines": groups,
        "totals": {
            "pipelines": len(groups),
            "runs": len({r["run_id"] for r in rows}),
            "stages": sum(g["stages"] for g in groups),
            "est_cost_usd": round(sum(g["est_cost_usd"] for g in groups), 4),
            "matched_sessions": len({r["session_id"] for r in rows if r["session_id"] in costs}),
            "run_dirs": len(discover_run_dirs(transcript_root_path, extra_roots=extra_roots)),
        },
        "project": project,
        "window": window.label if window is not None else None,
    }
