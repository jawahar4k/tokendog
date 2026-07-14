from __future__ import annotations
import argparse
import os
from .backend import LocalSQLiteBackend, QueryFilter
from .ingest import ingest_sink, ingest_glitch_firmware
from .sink import read_events
from .approx import _encoding
from .config import tokendog_home
from .templates import apply_templates, repo_templates_dir


def cost_summary(group_by="runtime", since=None, until=None, glitch_db=None) -> dict:
    backend = LocalSQLiteBackend(path=":memory:")
    ingest_sink(backend)
    if glitch_db:
        ingest_glitch_firmware(backend, glitch_db)
    rollup = backend.query(QueryFilter(group_by=group_by, since=since, until=until))
    return {"group_by": rollup.group_by,
            "rows": [r.__dict__ for r in rollup.rows]}


def format_rollup(summary: dict) -> str:
    lines = [f"### TokenDog cost by {summary['group_by']}",
             "",
             "| Group | Calls | In | Out | Est $ |",
             "|---|--:|--:|--:|--:|"]
    for r in summary["rows"]:
        lines.append("| {key} | {calls} | {input_tokens} | {output_tokens} | ${est_cost_usd:.4f} |".format(**r))
    if len(lines) == 4:
        lines.append("| _(no data yet)_ | | | | |")
    lines.append("")
    lines.append("_Numbers are local tiktoken approximations, not authoritative billing._")
    return "\n".join(lines)


def doctor_report(cwd: str) -> str:
    home = tokendog_home()
    n_events = sum(1 for _ in read_events())
    tik = "available" if _encoding() else "MISSING (falling back to len/4) — run `pip install tiktoken`"
    glitch = os.path.join(cwd, ".glitch", "firmware", "firmware.db")
    glitch_status = "present" if os.path.exists(glitch) else "not found"
    return "\n".join([
        "TokenDog doctor",
        f"- state dir: {home}",
        f"- telemetry events recorded: {n_events}",
        f"- tiktoken: {tik}",
        f"- glitch firmware.db: {glitch_status} ({glitch})",
    ])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tokendog")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cost")
    c.add_argument("--group-by", default="runtime")
    c.add_argument("--since")
    c.add_argument("--until")
    c.add_argument("--glitch-db")

    sub.add_parser("doctor")

    b = sub.add_parser("budget")
    b.add_argument("--set-daily", type=float)
    b.add_argument("--set-session", type=float)
    b.add_argument("--set-alert", type=float)
    b.add_argument("--set-webhook")
    b.add_argument("--show", action="store_true")

    a = sub.add_parser("audit")
    a.add_argument("--session")

    i = sub.add_parser("init")
    i.add_argument("--target", default=".")
    i.add_argument("--force", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "cost":
        print(format_rollup(cost_summary(args.group_by, args.since, args.until, args.glitch_db)))
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
        gb = "tool"
        rollup = cost_summary(group_by=gb)
        if args.session:
            rollup = {"group_by": "session_id",
                      "rows": [r for r in cost_summary(group_by="session_id")["rows"] if r["key"] == args.session]}
        print(format_rollup(rollup))
    elif args.cmd == "init":
        written = apply_templates(repo_templates_dir(), args.target, force=args.force)
        for p in written:
            print(f"wrote {p}")
        print("Edit CLAUDE.md below the TOKENDOG_EXTENSION_MARKER for org-specific instructions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
