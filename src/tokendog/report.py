from __future__ import annotations
import argparse
import os
from .backend import LocalSQLiteBackend, QueryFilter
from .ingest import ingest_sink, ingest_glitch_firmware, ingest_transcripts
from .sink import read_events
from .approx import _encoding
from .config import tokendog_home
from .templates import apply_templates, repo_templates_dir


def cost_summary(group_by="runtime", since=None, until=None, glitch_db=None,
                 transcript_root=None, project=None) -> dict:
    backend = LocalSQLiteBackend(path=":memory:")
    # Transcripts and Glitch carry authoritative usage and are what gets priced.
    # The hook sink is loaded too, but only contributes tool-payload volume —
    # its bytes are already billed by the transcript, so it is never re-priced.
    ingest_transcripts(backend, transcript_root)
    ingest_sink(backend)
    if glitch_db:
        ingest_glitch_firmware(backend, glitch_db)
    rollup = backend.query(QueryFilter(group_by=group_by, since=since,
                                       until=until, project=project))
    return {"group_by": rollup.group_by, "project": project,
            "note": BILLING_NOTE,
            "rows": [r.__dict__ for r in rollup.rows]}


BILLING_NOTE = (
    "Est $ is API list-price attribution, not an invoice. On a Claude subscription "
    "(Pro/Max) there is no per-token charge, so read it as relative weight — which "
    "work costs what — rather than money owed. It is also not a rate-limit proxy: it "
    "applies price weights (output 5x input, cache read 0.1x) that quota accounting "
    "does not.")

# Markdown form for rendered tables; the plain form travels in the API payload
# so an in-session consumer (the MCP tool) gets the caveat too. Every path that
# shows a dollar figure must carry it, or the caveat is decoration.
BILLING_CAVEAT = "_" + BILLING_NOTE.replace(
    "Est $", "`Est $`", 1).replace(
    "API list-price attribution", "**API list-price attribution**", 1) + "_"


def format_rollup(summary: dict) -> str:
    """Render the four billing buckets, not one collapsed total.

    Cache-write is the bucket that grows with context churn and is the one a
    developer can actually move; a scalar total hides it.
    """
    scope = f" — project `{summary['project']}`" if summary.get("project") else ""
    lines = [f"### TokenDog cost by {summary['group_by']}{scope}",
             "",
             "| Group | Calls | Cache write | Cache read | Out | Fresh in | Est $ |",
             "|---|--:|--:|--:|--:|--:|--:|"]
    for r in summary["rows"]:
        lines.append(
            "| {key} | {calls} | {cache_creation_tokens} | {cache_read_tokens} | "
            "{output_tokens} | {input_tokens} | ${est_cost_usd:.4f} |".format(**r))
    if len(lines) == 4:
        lines.append("| _(no data yet)_ | | | | | | |")
    lines.append("")
    lines.append("_Cost is priced from authoritative per-turn usage (Claude Code "
                 "transcripts, Glitch). Hook events contribute tool-payload volume "
                 "only and are not priced — their bytes are already billed by the "
                 "turn that carries them._")
    lines.append("")
    lines.append(BILLING_CAVEAT)
    return "\n".join(lines)


def format_tool_audit(summary: dict) -> str:
    """Per-tool payload volume — deliberately NOT a per-tool cost table.

    A transcript turn is where the money is, and it carries no tool name; the
    hook events that DO name a tool are never priced, because their bytes are
    billed by the turn that follows them. So a per-tool dollar column can only
    ever be $0.00, and rendering one prints structural zeros that read as
    measured ones. This shows the thing that is actually known: how much text
    each tool put into the context, approximately.
    """
    rows = [r for r in summary["rows"]
            if r["key"] != "(none)" and r["tool_payload_tokens"] > 0]
    rows.sort(key=lambda r: -r["tool_payload_tokens"])
    total = sum(r["tool_payload_tokens"] for r in rows)
    scope = f" — project `{summary['project']}`" if summary.get("project") else ""
    lines = [f"### TokenDog tool payload volume{scope}", "",
             "| Tool | Calls | Payload tokens | % |",
             "|---|--:|--:|--:|"]
    for r in rows:
        share = (r["tool_payload_tokens"] / total * 100) if total else 0.0
        lines.append(f"| {r['key']} | {r['calls']:,} | "
                     f"{r['tool_payload_tokens']:,} | {share:.1f}% |")
    if not rows:
        lines.append("| _(no tool events yet — are the hooks running? "
                     "`tokendog doctor`)_ | | | |")
    lines.append("")
    lines.append("_Approximate (tiktoken) size of each tool's payloads, for attribution. "
                 "There is no per-tool dollar figure and this is not an omission: cost is "
                 "priced per metered turn, and a turn carries no tool name. These bytes are "
                 "billed by the turn that carries them — see `tokendog cost`._")
    return "\n".join(lines)


def format_savings(s: dict) -> str:
    lines = [
        "### TokenDog savings — with vs without truncation",
        "",
        f"- truncation events: {s['events']}",
        f"- tokens saved: {s['total_saved']:,} of {s['total_original']:,} "
        f"truncated-call tokens ({s['pct']:.1f}%)",
    ]
    if s["modes"]:
        lines.append("- modes: " + ", ".join(f"{k}={v}" for k, v in sorted(s["modes"].items())))
    if s["per_tool"]:
        lines += ["", "| Tool | Tokens saved |", "|---|--:|"]
        for tool, saved in sorted(s["per_tool"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| {tool} | {saved:,} |")
    if s["events"] == 0:
        lines.append("")
        lines.append("_No truncations recorded yet — nothing has been altered._")
    else:
        lines.append("")
        lines.append("_shadow rows are PROJECTED savings; output was NOT modified. "
                     "enforce rows were applied._")
    return "\n".join(lines)


def band_report_data(transcript_root=None, project=None) -> dict:
    from itertools import chain
    from .bands import band_summary
    from .transcripts import read_transcripts
    events = chain(read_transcripts(transcript_root), read_events())
    if project:
        events = (e for e in events if e.project == project)
    data = band_summary(events)
    data["project"] = project
    return data


def format_bands(s: dict) -> str:
    """Show where the tokens actually are: concentrated in the big turns.

    Roll-ups by runtime/tool/session/model answer "who spent it". This answers
    "how big was the context when it was spent" — the number a developer can
    act on, because everything in a context is re-read on every later turn.
    """
    scope = f" — project `{s['project']}`" if s.get("project") else ""
    lines = [f"### TokenDog context bands{scope}", ""]
    if not s["turns"]:
        lines.append("_No metered turns found. Context bands are read from Claude Code "
                     "transcripts — run `tokendog doctor` to check they were located._")
        return "\n".join(lines)

    lines.append(f"{s['turns']:,} turns · {s['total_context_tokens']:,} context tokens")
    lines.append("")
    lines.append("| Band | Turns | % turns | Context tokens | % tokens |")
    lines.append("|---|--:|--:|--:|--:|")
    for r in s["rows"]:
        lines.append(f"| {r['band']} | {r['turns']:,} | {r['pct_turns']:.1f}% | "
                     f"{r['context_tokens']:,} | {r['pct_tokens']:.1f}% |")

    c = s["concentration"]
    lines.append("")
    lines.append(f"**{c['pct_turns']:.1f}% of turns carry {c['pct_tokens']:.1f}% of "
                 f"context tokens** (turns at or above {c['threshold']:,} tokens).")

    p = s["peak_context"]
    lines.append("")
    lines.append(f"Peak context per {p['unit']} — p50 {p['p50']:,} · p90 {p['p90']:,} · "
                 f"max {p['max']:,} (across {p['count']:,} {p['unit']}s)")
    lines.append("")
    lines.append("_Context = cache read + cache write + fresh input, per metered turn. "
                 "Output is excluded: it came back, it was not carried._")
    return "\n".join(lines)


def doctor_report(cwd: str) -> str:
    from .transcripts import transcript_root, known_projects
    from .sink import sink_health, retention_days, max_sink_bytes
    home = tokendog_home()
    n_events = sum(1 for _ in read_events())
    tik = "available" if _encoding() else "MISSING (falling back to len/4) — run `pip install tiktoken`"
    glitch = os.path.join(cwd, ".glitch", "firmware", "firmware.db")
    glitch_status = "present" if os.path.exists(glitch) else "not found"
    troot = transcript_root()
    if troot.exists():
        n_transcripts = sum(1 for _ in troot.rglob("*.jsonl"))
        projects = known_projects()
        shown = ", ".join(projects[:8]) + ("…" if len(projects) > 8 else "")
        transcript_status = (f"{n_transcripts} file(s) at {troot}\n"
                             f"    - covers {len(projects)} project(s) on this machine, "
                             f"not just the current one: {shown}\n"
                             f"    - scope one with `tokendog cost --project <name>` "
                             f"(name = the project directory's basename)")
    else:
        transcript_status = f"NOT FOUND at {troot} — cost will read $0 without it"

    h = sink_health()
    if h["degraded"]:
        sink_status = (f"DEGRADED — {h['failures']} failed write(s) across "
                       f"{h['sessions']}{'+' if h['sessions_capped'] else ''} session(s) "
                       f"since {h['first_ts']}; last reason: {h['last_reason']}")
    else:
        sink_status = (f"ok (cap {max_sink_bytes() // (1024 * 1024)} MB/day, "
                       f"retention {retention_days()} days)")

    # Active-vs-off state for everything that can affect model output.
    observe_only = str(os.environ.get("TOKENDOG_OBSERVE_ONLY")).strip().lower() in ("1", "true", "yes", "on")
    trunc = os.environ.get("TOKENDOG_TRUNCATE_MODE", "off").strip().lower()
    if observe_only:
        trunc = "shadow (forced by TOKENDOG_OBSERVE_ONLY)"
    try:
        from .budget import load_budget
        b = load_budget()
        budget_on = any(v is not None for v in (b.daily_usd, b.session_usd))
    except Exception:
        budget_on = False
    budget_state = "enforcing" if (budget_on and not observe_only) else ("set but observe-only" if budget_on else "off (no budget set)")

    return "\n".join([
        "TokenDog doctor",
        f"- state dir: {home}",
        f"- telemetry events recorded: {n_events} (hook volume; not billable)",
        f"- sink health: {sink_status}",
        f"- claude code transcripts (authoritative cost): {transcript_status}",
        f"- tiktoken: {tik}",
        "- cost figures: API list-price attribution, not an invoice — on a Pro/Max "
        "subscription there is no per-token charge, so read them as relative weight",
        f"- glitch firmware.db: {glitch_status} ({glitch})",
        "- quality-affecting features (off unless you opt in):",
        f"    - output truncation: {trunc}",
        f"    - budget enforcement: {budget_state}",
    ])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tokendog")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cost")
    c.add_argument("--group-by", default="runtime")
    c.add_argument("--since")
    c.add_argument("--until")
    c.add_argument("--glitch-db")
    c.add_argument("--project", help="restrict to one project (transcript cwd basename)")

    sub.add_parser("doctor")

    b = sub.add_parser("budget")
    b.add_argument("--set-daily", type=float)
    b.add_argument("--set-session", type=float)
    b.add_argument("--set-alert", type=float)
    b.add_argument("--set-webhook")
    b.add_argument("--show", action="store_true")

    a = sub.add_parser("audit")
    a.add_argument("--session")
    a.add_argument("--project")

    sub.add_parser("savings")

    bd = sub.add_parser("bands")
    bd.add_argument("--project")

    i = sub.add_parser("init")
    i.add_argument("--target", default=".")
    i.add_argument("--force", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "cost":
        print(format_rollup(cost_summary(args.group_by, args.since, args.until,
                                         args.glitch_db, project=args.project)))
    elif args.cmd == "doctor":
        print(doctor_report(os.getcwd()))
    elif args.cmd == "budget":
        from .budget import load_budget, save_budget, Budget, check
        cur = load_budget()
        if any(v is not None for v in (args.set_daily, args.set_session, args.set_alert, args.set_webhook)):
            cur = Budget(
                daily_usd=args.set_daily if args.set_daily is not None else cur.daily_usd,
                session_usd=args.set_session if args.set_session is not None else cur.session_usd,
                alert_usd=args.set_alert if args.set_alert is not None else cur.alert_usd,
                webhook_url=args.set_webhook if args.set_webhook is not None else cur.webhook_url,
            )
            save_budget(cur)
        glitch = os.path.join(os.getcwd(), ".glitch", "firmware", "firmware.db")
        st = check(glitch_db=glitch if os.path.exists(glitch) else None)
        print(f"TokenDog budget — daily=${cur.daily_usd} alert=${cur.alert_usd} "
              f"session=${cur.session_usd} webhook={'set' if cur.webhook_url else 'none'}")
        print(f"Today so far: ${st['daily']:.4f}"
              + ("  [OVER DAILY]" if st['over_daily'] else "")
              + ("  [OVER ALERT]" if st['over_alert'] else ""))
    elif args.cmd == "audit":
        rollup = cost_summary(group_by="tool", project=args.project)
        if not args.session:
            print(format_tool_audit(rollup))
            return 0
        if args.session:
            rollup = {"group_by": "session_id",
                      "rows": [r for r in cost_summary(group_by="session_id",
                                                       project=args.project)["rows"]
                               if r["key"] == args.session]}
        print(format_rollup(rollup))
    elif args.cmd == "savings":
        from .savings import savings_summary
        print(format_savings(savings_summary()))
    elif args.cmd == "bands":
        print(format_bands(band_report_data(project=args.project)))
    elif args.cmd == "init":
        written = apply_templates(repo_templates_dir(), args.target, force=args.force)
        for p in written:
            print(f"wrote {p}")
        print("Edit CLAUDE.md below the TOKENDOG_EXTENSION_MARKER for org-specific instructions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
