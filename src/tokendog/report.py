from __future__ import annotations
import argparse
import json
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


def effect_data(split=None, project=None, transcript_root=None) -> dict:
    from .effect import (cache_efficiency, compare, iter_tool_calls,
                         position_curve, split_calls, tool_summary)
    calls = list(iter_tool_calls(transcript_root, project=project))
    data = {"split": split, "project": project,
            "curve": position_curve(transcript_root, project=project)}
    if split:
        before, after = split_calls(calls, split)
        data["comparison"] = compare(tool_summary(before), tool_summary(after))
        data["before_calls"], data["after_calls"] = len(before), len(after)
    data["summary"] = tool_summary(calls)
    data["cache"] = cache_efficiency(transcript_root, project=project)
    return data


def format_effect(d: dict) -> str:
    """Report whether an intervention moved anything — or say it did not.

    A flat number here is a real result, not a failure to measure. The point of
    the ledger is that "we changed something and nothing happened" becomes
    visible instead of being indistinguishable from "we never looked".
    """
    scope = f" — project `{d['project']}`" if d.get("project") else ""
    lines = [f"### TokenDog intervention ledger{scope}", ""]

    curve = d["curve"]["buckets"]
    if curve:
        lines += ["**Cost by turn position** — the compounding, priced.", "",
                  "| Turn # in session | Turns | $/turn | Context/turn |",
                  "|---|--:|--:|--:|"]
        for b in curve:
            lines.append(f"| {b['label']} | {b['turns']:,} | "
                         f"${b['cost_per_turn']:.4f} | {b['context_per_turn']:,} |")
        ratio = d["curve"]["ratio"]
        if ratio:
            lines += ["", f"A turn late in a session costs **{ratio:.1f}x** one at the "
                          "start. That multiple is the case for splitting a long stage — "
                          "the work is the same, the carried context is not."]
        lines.append("")

    c = d.get("comparison")
    if c:
        lines += [f"**Tool payloads, before vs on/after {d['split']}** "
                  f"({d['before_calls']:,} → {d['after_calls']:,} calls)", "",
                  "| Tool | Calls before → after | Mean bytes before → after | Change | Scoped before → after |",
                  "|---|--:|--:|--:|--:|"]
        for name, v in sorted(c["tools"].items(),
                              key=lambda kv: -(kv[1]["after_calls"])):
            chg = f"{v['change_pct']:+.0f}%" if v["change_pct"] is not None else "—"
            def pct(x):
                return "n/a" if x is None else f"{x:.0f}%"
            lines.append(
                f"| {name} | {v['before_calls']:,} → {v['after_calls']:,} | "
                f"{v['before_mean']:,} → {v['after_mean']:,} | {chg} | "
                f"{pct(v['before_scoped_pct'])} → {pct(v['after_scoped_pct'])} |")
        overall = c["change_pct"]
        if overall is not None:
            verdict = ("no measurable change" if abs(overall) < 5
                       else ("smaller payloads" if overall < 0 else "larger payloads"))
            lines += ["", f"Overall mean payload {c['before_mean']:,} → "
                          f"{c['after_mean']:,} bytes ({overall:+.1f}%) — **{verdict}**."]
        lines.append("")

    cache = d.get("cache")
    if cache:
        better = cache["net_usd"] < 0
        lines += ["**Cache TTL** — is the 2x 1-hour write premium being earned?", "",
                  f"- writes {(cache['write_5m'] + cache['write_1h']) / 1e6:.1f}M tokens "
                  f"against {cache['reads'] / 1e6:.0f}M reads "
                  f"({cache['write_read_pct']:.1f}%)",
                  f"- as billed: ${cache['actual_usd']:,.0f}",
                  f"- everything on the 5m TTL: ${cache['all_5m_usd']:,.0f}, but "
                  f"{cache['expiring_gaps']:,} gaps of 5-60 min would rebuild the prefix "
                  f"(${cache['rebuild_usd']:,.0f})",
                  f"- net: **${cache['net_usd']:+,.0f}** — the shorter TTL would be "
                  f"{'CHEAPER' if better else 'more expensive'}"]
        if not better:
            lines.append("")
            lines.append("The 1-hour TTL is the right call here. Cache-write cost is "
                         "then the price of caching working, not waste to be optimised.")
        lines.append("")

    lines.append("_Measured from Claude Code transcripts, retroactively — no hooks and "
                 "no instrumentation, so a change made last week can still be checked. "
                 "`Scoped` is the share of calls that narrowed their own fetch "
                 "(`Read` with offset/limit, `Grep` with a path/glob/head_limit), "
                 "counted only for tools where the record can answer it._")
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


def band_report_data(transcript_root=None, project=None, *, window=None) -> dict:
    from itertools import chain
    from .bands import band_summary
    from .transcripts import read_transcripts
    from .window import scoped
    events = chain(read_transcripts(transcript_root), read_events())
    if project:
        events = (e for e in events if e.project == project)
    data = band_summary(scoped(events, window))
    data["project"] = project
    data["window"] = window.label if window is not None else None
    return data


def format_bands(s: dict) -> str:
    """Show where the tokens actually are: concentrated in the big turns.

    Roll-ups by runtime/tool/session/model answer "who spent it". This answers
    "how big was the context when it was spent" — the number a developer can
    act on, because everything in a context is re-read on every later turn.
    """
    scope = f" — project `{s['project']}`" if s.get("project") else ""
    lines = [f"### TokenDog context bands{scope}", ""] + _window_lines(s)
    if not s["turns"]:
        # An empty windowed report is an answer, not a fault. Sending the reader
        # to `doctor` because nothing happened in the sixteen minutes they asked
        # about would have them debug a working install.
        lines.append(f"_No metered turns in this window ({s['window']})._" if s.get("window")
                     else "_No metered turns found. Context bands are read from Claude "
                          "Code transcripts — run `tokendog doctor` to check they were "
                          "located._")
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


def format_resumes(s: dict) -> str:
    """Show where a large window was carried past the moment to reset it.

    Bands say a turn was expensive. This says a turn was expensive AND
    avoidable: the session had already stopped, and starting again in the same
    window is what made every turn after it cost what it did.
    """
    scope = f" — project `{s['project']}`" if s.get("project") else ""
    t = s["totals"]
    th = s["thresholds"]
    lines = [f"### TokenDog resumes at the wall{scope}", ""] + _window_lines(s)
    if not t["count"]:
        lines.append(f"_No resume found: no turn at or above {th['min_context']:,} tokens "
                     f"of context followed a pause of {th['gap_minutes']:.0f}+ minutes. "
                     "This is the result you want._")
        return "\n".join(lines)

    lines.append(f"{t['count']:,} resume(s) across {t['sessions']} session(s) · "
                 f"{t['carried_tokens']:,} tokens carried past a reset")
    lines.append("")
    lines.append("| Session | Project | Resumes | Escalating | First → last context | Carried |")
    lines.append("|---|---|--:|:-:|--:|--:|")
    for r in s["sessions"]:
        arrow = f"{r['first_context']:,} → {r['last_context']:,}"
        lines.append(f"| `{r['session'][:8]}` | {r['project'] or '—'} | {r['count']} | "
                     f"{'yes' if r['escalating'] else 'no'} | {arrow} | "
                     f"{r['carried_tokens']:,} |")

    worst = s["sessions"][0]
    lines.append("")
    if worst["escalating"]:
        lines.append(f"**`{worst['session'][:8]}` escalated**: its resumes ended higher than "
                     f"they started, {worst['first_context']:,} → {worst['last_context']:,}. "
                     "Not resetting is what raised the floor for the next one.")
    if t["never_reset"]:
        lines.append(f"{t['never_reset']:,} of {t['count']:,} resume(s) were never followed by "
                     "a reset — those windows were carried to the end of the session.")

    lines.append("")
    lines.append(f"_A resume is a pause of {th['gap_minutes']:.0f}+ minutes followed by a turn "
                 f"carrying {th['min_context']:,}+ tokens. The transcript records no reason for a "
                 "pause, so this names the shape, not the cause._")
    lines.append("")
    lines.append("_`Carried` is occupancy at the resume times the turns that then ran before the "
                 "next decision point — the next reset or the next resume, whichever came first. "
                 "Segments therefore never overlap, so each turn is charged to exactly one resume. "
                 "It is what resetting at that moment could **at most** have avoided: a real reset "
                 "re-reads some of the same material, so treat it as a ceiling, not a saving._")
    return "\n".join(lines)


def format_cold_start(s: dict) -> str:
    """Show discovery paid for more than once.

    Bands and resumes both look at one session carrying too much. This looks
    across sessions that each carry too little, repeatedly: a headless run
    starts empty every time, so running it eleven times over one repository
    pays to learn that repository eleven times.
    """
    scope = f" — project `{s['project']}`" if s.get("project") else ""
    t = s["totals"]
    th = s["thresholds"]
    lines = [f"### TokenDog cold-start duplication{scope}", ""] + _window_lines(s)
    if not t["projects"]:
        seen = t["headless_runs_seen"]
        lines.append(f"_No repeated discovery found across {seen:,} non-interactive run(s). "
                     f"A project needs {th['min_runs']}+ runs sharing it before duplication "
                     "is possible._")
        return "\n".join(lines)

    lines.append(f"{t['runs']:,} non-interactive run(s) across {t['projects']} project(s) · "
                 f"{t['duplicated_tokens']:,} tokens of repeated discovery")
    lines.append("")
    lines.append("| Project | Runs | Ramp turns (mean) | Total ramp | Duplicated | Shared files |")
    lines.append("|---|--:|--:|--:|--:|--:|")
    for g in s["groups"]:
        shared = f"{g['shared_file_count']}" if g["shared_file_count"] else "—"
        lines.append(f"| {g['project']} | {g['runs']} | {g['mean_ramp_turns']} | "
                     f"{g['ramp_tokens']:,} | {g['duplicated_tokens']:,} | {shared} |")

    worst = s["groups"][0]
    lines.append("")
    lines.append(f"**{worst['project']}**: {worst['runs']} runs each climbed roughly "
                 f"{worst['mean_ramp_turns']} turns to get up to speed. One of them had to; "
                 f"the other {worst['runs'] - 1} were re-reading what the first already knew.")
    if worst["shared_files"]:
        names = ", ".join(f"`{f['file']}` ({f['runs']} runs)"
                          for f in worst["shared_files"][:5])
        lines.append("")
        lines.append(f"Files more than one run touched: {names}.")
    else:
        lines.append("")
        lines.append("_No hook telemetry for these runs, so the shared reads are inferred from "
                     "the ramp rather than observed. Install the plugin to name the files._")

    lines.append("")
    lines.append(f"_A run's ramp is the turns before occupancy first reached "
                 f"{th['ramp_peak_share']:.0%} of that run's own peak — the climb to get up to "
                 "speed. `Duplicated` is the group's whole ramp minus its cheapest single ramp: "
                 "one run genuinely had to discover, so only the rest are counted._")
    lines.append("")
    lines.append("_Fix: extract what the runs share once and pass it into each run's prompt, "
                 "rather than letting every run rediscover it._")
    return "\n".join(lines)


def _label(transcript: str) -> str:
    """A short transcript id that stays unique.

    Eight characters is plenty for a session uuid and useless for a subagent:
    the `agent-` prefix eats six of them, leaving two hex digits, and a single
    fan-out produces dozens. Measured on one machine, every 8-char subagent
    label collided — sixteen labels covering 376 transcripts, 18 to 35 rows
    each, all rendered as the same handful of names. So the prefix is kept and
    eight characters are taken from what follows it.
    """
    if transcript.startswith("agent-"):
        return "agent-" + transcript[len("agent-"):][:8]
    return transcript[:8]


def _window_lines(s: dict) -> list[str]:
    """The window a report was scoped to, named in the reader's own local time.

    Printed rather than assumed: a table of figures with no stated range is
    read as all-time, and a reader who scoped a report and forgot will
    misattribute every number in it.
    """
    win = s.get("window")
    return [f"**Window:** {win}", ""] if win else []


def _tok(n) -> str:
    """A token count at reading size: 13.2M, 610K, 27,431."""
    if n is None:
        return "—"
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 10_000:
        return f"{n/1_000:.0f}K"
    return f"{n:,}"


def _rate(per_min) -> str:
    """Tokens per minute, the figure that separates a runaway from a grinder."""
    if per_min is None:
        return "—"
    if per_min >= 1_000_000:
        return f"{per_min/1_000_000:.2f}M/m"
    if per_min >= 1_000:
        return f"{per_min/1_000:.0f}K/m"
    return f"{per_min:.0f}/m"


def _hrs(h) -> str:
    return f"{h/24:.0f}d" if h >= 24 else f"{h:.1f}h"


def format_errors(d: dict) -> str:
    """Which tools fail, how often, and how — so a costly retry loop is visible.

    A failing tool bills twice (the failure, then the bigger-context retry).
    Categories are matched by pattern, not read by a model, so this is free.
    """
    scope = f" — project `{d['project']}`" if d.get("project") else ""
    t = d["totals"]
    lines = [f"### TokenDog tool errors{scope}", ""] + _window_lines(d)
    if not t["calls"]:
        lines.append("_No tool results found. Errors are read from transcripts — a tool_result "
                     "carries success/failure, matched to the tool_use that named the tool._")
        return "\n".join(lines)
    rate = round(t["errors"] / t["calls"] * 100, 1) if t["calls"] else 0.0
    lines.append(f"{t['calls']:,} tool call(s) · {t['errors']:,} failed ({rate}%) · "
                 f"{t['high_error_tools']} tool(s) failing often")
    lines.append("")
    lines.append("| Tool | Calls | Errors | Rate | Mostly | Example |")
    lines.append("|---|--:|--:|--:|---|---|")
    for r in d["tools"]:
        if not r["errors"]:
            continue
        flag = " ⚠" if r["high_error"] else ""
        ex = (r["sample"][:60] + "…") if r["sample"] and len(r["sample"]) > 61 else (r["sample"] or "")
        lines.append(f"| `{r['tool']}`{flag} | {r['calls']:,} | {r['errors']:,} | "
                     f"{r['error_rate']}% | {r['dominant'] or '—'} | {ex} |")
    lines.append("")
    lines.append("_⚠ marks a tool with 5+ calls failing 20%+ of the time — a pattern, not a one-off. "
                 "Categories (timeout / not-found / permission / network / rate-limit / syntax / "
                 "interrupted / nonzero-exit / other) are matched by pattern, not read by a model._")
    return "\n".join(lines)


def format_floor(d: dict) -> str:
    """The context floor budget: every always-resident item, sized, used-or-not.

    Answers the question the context ledger can't: what rides in every turn
    before you type, and which lines am I paying for and not using. A floor item
    is re-read on every turn, so its real weight is size x turns — a small schema
    resident for thousands of turns can outweigh a big one used once.
    """
    scope = f" — project `{d['project']}`" if d.get("project") else ""
    t = d["totals"]
    lines = [f"### TokenDog context floor{scope}", ""] + _window_lines(d)
    if not d["items"]:
        lines.append("_Nothing measured. The floor is MCP schemas, skills and instruction files — "
                     "run `tokendog surface --refresh` to size connector schemas._")
        return "\n".join(lines)
    head = [f"{_tok(t['floor_known'])} known floor / turn"]
    if t["reclaim_items"]:
        head.append(f"{_tok(t['reclaimable_per_turn'])} reclaimable ({t['reclaim_items']} dead item(s))")
    if t["carried_wasted"]:
        head.append(f"{_tok(t['carried_wasted'])} already spent on dead weight")
    lines.append(" · ".join(head))
    if not t["have_inventory"]:
        lines.append("")
        lines.append("_MCP schema sizes are blank until measured — run `tokendog surface --refresh`._")
    lines.append("")
    lines.append("| Item | Kind | Per turn | Used | Carried | Verdict | |")
    lines.append("|---|---|--:|:-:|--:|---|---|")
    MARK = {"reclaim": "■ reclaim", "trim": "△ trim", "idle": "△ idle",
            "unmeasured": "measure", "keep": "✓ keep", "off": "off"}
    for i in d["items"]:
        used = "yes" if i["used"] else "no"
        pt = _tok(i["per_turn"]) if i["per_turn"] is not None else "—"
        carr = _tok(i["carried"]) if i.get("carried") else "—"
        note = i.get("detail", "")
        lines.append(f"| `{i['item']}` | {i['kind']} | {pt} | {used} | {carr} | "
                     f"{MARK.get(i['verdict'], i['verdict'])} | {note} |")
    lines.append("")
    lines.append("_Per turn = tokens this item adds to EVERY request. Carried = size x turns "
                 "resident, what it has cost so far. `■ reclaim` = resident and never used "
                 "(disable it / drop the skill); `△ trim` = used but a heavy always-on block; "
                 "`✓ keep` = earning its place. Skills charge their frontmatter every turn even "
                 "when never invoked; instruction files are always-on by design._")
    return "\n".join(lines)


def format_discovery(d: dict) -> str:
    """How much of each session was FINDING vs DOING — the grep-loop flag.

    A high ratio over many calls means the model rebuilt a map of the code it has
    no memory of, one search per turn. The fix is upfront context (a CLAUDE.md
    layout, a code-search tool), not fewer tool calls — this just makes the
    pattern visible, and movable once you change something.
    """
    scope = f" — project `{d['project']}`" if d.get("project") else ""
    t = d["totals"]
    lines = [f"### TokenDog discovery ratio{scope}", ""] + _window_lines(d)
    if not t["sessions"]:
        lines.append("_No tool calls found in scope._")
        return "\n".join(lines)
    ov = t["overall_ratio"]
    gen = t.get("gen_share")
    lines.append(f"{t['sessions']:,} session(s) · overall {ov}% discovery "
                 f"({t['discovery_calls']:,} find / {t['work_calls']:,} do, {t['grep_calls']:,} greps) · "
                 f"{t['high_discovery_sessions']} mostly-exploring"
                 + (f" · gen {gen}% of tokens" if gen is not None else ""))
    lines.append("")
    lines.append("| Session | Project | Calls | Find | Do | greps | Discovery | Gen % | |")
    lines.append("|---|---|--:|--:|--:|--:|--:|--:|---|")
    for r in d["sessions"][:15]:
        if r["acted"] < d["min_tool_calls"] and not r["high_discovery"]:
            continue
        flag = "⚠ mostly exploring" if r["high_discovery"] else ""
        ratio = "—" if r["discovery_ratio"] is None else f"{r['discovery_ratio']}%"
        gs = "—" if r.get("gen_share") is None else f"{r['gen_share']}%"
        lines.append(f"| `{r['session']}` | {r['project'] or '—'} | {r['tool_calls']:,} | "
                     f"{r['discovery']:,} | {r['work']:,} | {r['grep']:,} | {ratio} | {gs} | {flag} |")
    lines.append("")
    lines.append(f"_Discovery = read-only exploration (grep/ls/find/cat, Read/Grep/Glob, git log/diff, "
                 f"web lookups); Do = edits/writes, builds, tests, commits. Ratio is over find+do calls "
                 f"only; delegation and MCP calls are excluded. ⚠ marks {d['min_tool_calls']}+ acting "
                 f"calls at 60%+ discovery — a session that needed a map it did not have._")
    lines.append(f"_`Gen %` is output (where the thinking budget bills) as a share of all tokens the "
                 f"session touched — the effort footprint. A low number means the session is context "
                 f"re-read, so changing the effort level cannot move its cost: the lever is less "
                 f"context, not less thinking._")
    return "\n".join(lines)


def format_pipelines(d: dict) -> str:
    """Claude spend attributed to Glitch pipelines, via the run-state files.

    The link is exact: Glitch stamps each stage's `--session-id`, which is the
    transcript name tokendog costs by — no heuristic. `Glitch $` is Glitch's own
    per-stage figure, shown beside tokendog's for a sanity check.
    """
    scope = f" — project `{d['project']}`" if d.get("project") else ""
    t = d["totals"]
    lines = [f"### TokenDog Glitch pipelines{scope}", ""] + _window_lines(d)
    if not d["pipelines"]:
        lines.append("_No Glitch runs found. This reads `<project>/.glitch/runs/*.json`; a project "
                     "with pipeline runs and Claude sessions in range will show here._"
                     if t["run_dirs"] else
                     "_No `.glitch/runs` directories under the projects that have sessions._")
        return "\n".join(lines)
    lines.append(f"{t['pipelines']} pipeline(s) · {t['runs']} run(s) · {t['stages']} Claude stage(s) · "
                 f"${t['est_cost_usd']:.2f} attributed · {t['matched_sessions']} session(s) matched")
    lines.append("")
    lines.append("| Pipeline | Runs | Stages | Sessions | Input | Est $ | Glitch $ |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for g in d["pipelines"]:
        lines.append(f"| {g['pipeline']} | {g['runs']} | {g['stages']} | {g['sessions']} | "
                     f"{_tok(g['input'])} | ${g['est_cost_usd']:.2f} | ${g['glitch_cost_usd']:.2f} |")
    lines.append("")
    lines.append("_Exact attribution: each stage's Claude `--session-id` (from the run-state file) is "
                 "the transcript tokendog priced. `Est $` is tokendog's list-price weight; `Glitch $` is "
                 "Glitch's own per-stage figure. " + BILLING_NOTE + "_")
    return "\n".join(lines)


def format_outcomes(d: dict) -> str:
    """Cost per merged PR, and what fed it — the ROI view.

    A heuristic join, and it says so: a session is linked to a commit that lands
    in its window in the same repo with overlapping files, and a commit to a PR
    by its merge subject. The cost per PR is the sum of the sessions that
    plausibly fed it; where a PR drew on several sessions the cost is shared, not
    split, because a finer apportionment would be invented.
    """
    scope = f" — project `{d['project']}`" if d.get("project") else ""
    t = d["totals"]
    lines = [f"### TokenDog outcomes{scope}", ""] + _window_lines(d)
    if not d["groups"]:
        lines.append("_No outcomes linked. This needs git: sessions are matched to commits in "
                     "their own repo, so it runs where the work landed, and a repo with no commits "
                     "in the window has nothing to attribute._")
        return "\n".join(lines)
    head = [f"{t['prs']:,} PR(s)"]
    if t["cost_per_pr"] is not None:
        head.append(f"${t['cost_per_pr']:.2f} per merged PR")
    head.append(f"${t['attributed_cost']:.2f} attributed")
    if t["unmatched_sessions"]:
        head.append(f"{t['unmatched_sessions']:,} session(s) unlinked (${t['unmatched_cost']:.2f})")
    ex, he = t.get("exact_commits", 0), t.get("heuristic_commits", 0)
    if ex or he:
        head.append(f"{ex} exact / {he} heuristic commit link(s)")
    lines.append(" · ".join(head))
    lines.append("")
    lines.append("| Outcome | Repo | Link | Commits | Sessions | Turns | Est $ |")
    lines.append("|---|---|---|--:|--:|--:|--:|")
    for g in d["groups"]:
        name = f"PR #{g['pr']}" if g["pr"] is not None else "_(no PR)_"
        subj = (g["subject"][:44] + "…") if len(g["subject"]) > 45 else g["subject"]
        label = f"{name} {subj}".strip()
        cost = f"${g['est_cost_usd']:.2f}" + ("*" if g["shared"] else "")
        mark = {"exact": "exact", "heuristic": "guess", "mixed": "mixed"}.get(g.get("method"), "—")
        lines.append(f"| {label} | {g['repo'] or '—'} | {mark} | {g['commit_count']} | "
                     f"{g['session_count']} | {g['turns']:,} | {cost} |")
    lines.append("")
    lines.append("")
    if he and not ex:
        lines.append("_These links are HEURISTIC (in-window + file overlap). For EXACT links, run "
                     "`tokendog install-hook` in the repo — commits then name their session outright._")
        lines.append("")
    lines.append("_Attribution is scoped to THIS machine's sessions and "
                 + ("this clone's git email" if d.get("own_author_only") else "all commit authors")
                 + ". It cannot see AI sessions run on other laptops, so a PR co-authored across "
                 + "machines shows only the share done here. For attribution that is exact rather "
                 + "than heuristic — and that aggregates across machines — stamp the session id into "
                 + "the commit (a `Claude-Session:` trailer); ask to enable that._")
    lines.append("")
    lines.append(f"_Cost per PR is the sum of the sessions that plausibly fed it. `*` marks a PR "
                 f"whose cost is SHARED across several sessions — the same dollars may appear under "
                 f"another PR those sessions also touched, so the per-PR figures do not sum to the "
                 f"total. Linkage is heuristic: in-window, same repo, overlapping files, PR number "
                 f"from the merge subject (grace {d['grace_minutes']:.0f}m). "
                 + BILLING_NOTE + "_")
    return "\n".join(lines)


def _hygiene_weight_block(s: dict) -> list[str]:
    """The same tokens again, split by what they actually cost.

    `Input` treats every token as one token. The bill does not: a cache read is
    a tenth of fresh input, a 5-minute cache write is 1.25x, a 1-hour write is
    2x. So two sessions carrying an identical total can differ twentyfold, and
    the column that says which is which is the one a reader chasing a limit
    needs. The split is only ever shown for the sessions where it changes the
    ranking — a table of five extra columns for every row would bury it.
    """
    rows = [r for r in s["sessions"] if r.get("weighted_input")]
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: -r["weighted_input"])[:8]
    t = s["totals"]
    b = t.get("buckets") or {}
    out = ["", "**Where the weight is** — the same tokens at their billing rates.", "",
           "| Session | Carried | Weighted | Cache read | 5m write | 1h write | Fresh |",
           "|---|--:|--:|--:|--:|--:|--:|"]
    for r in rows:
        rb = r.get("buckets") or {}
        out.append(
            f"| `{_label(r['transcript'])}` | {_tok(r['input'])} | "
            f"{_tok(r['weighted_input'])} | {_tok(rb.get('cache_read'))} | "
            f"{_tok((rb.get('write_5m') or 0) + (rb.get('write_other') or 0))} | "
            f"{_tok(rb.get('write_1h'))} | {_tok(rb.get('fresh'))} |")
    out.append(f"| **all** | **{_tok(t.get('input'))}** | "
               f"**{_tok(t.get('weighted_input'))}** | {_tok(b.get('cache_read'))} | "
               f"{_tok((b.get('write_5m') or 0) + (b.get('write_other') or 0))} | "
               f"{_tok(b.get('write_1h'))} | {_tok(b.get('fresh'))} |")

    hour = b.get("write_1h") or 0
    weighted = t.get("weighted_input") or 0
    if hour and weighted:
        share = hour * 2.0 / weighted * 100
        out += ["", f"_1-hour cache writes are {_tok(hour)} of the carried tokens but "
                    f"{share:.0f}% of the weight, because they bill at 2x. They buy latency "
                    "on a session whose turns are minutes apart; on a session that is simply "
                    "large they double the price of being large._"]
    return out


def format_hygiene(s: dict) -> str:
    """Show which sessions were managed and what not managing them cost.

    Bands size the windows; this asks whether anyone ever reset them. `Excess`
    is the only avoidable figure in the tool that needs no counterfactual: it
    counts tokens carried ABOVE a line the session could have held, so it never
    guesses what a reset would have re-read.
    """
    scope = f" — project `{s['project']}`" if s.get("project") else ""
    t = s["totals"]
    th = s["thresholds"]
    win = s.get("window")
    lines = [f"### TokenDog session hygiene{scope}", ""]
    if win:
        lines += [f"**Window:** {win}", ""]
    if not t["sessions"]:
        lines.append("_No sessions found." if not win else
                     f"_No turns in this window ({win})._")
        if not win:
            lines.append("Hygiene is read from transcripts — run "
                         "`tokendog doctor` to check they were located._")
        return "\n".join(lines)

    head = [f"{t['sessions']:,} session(s)"]
    if s.get("turns"):
        head.append(f"{s['turns']:,} turns")
    head.append(f"{_tok(t.get('input'))} carried, {_tok(t.get('weighted_input'))} weighted")
    span = s.get("window_minutes")
    if span:
        # The rate over the WHOLE window, not the busiest session in it: this is
        # the figure that answers "how long until the limit goes again".
        head.append(f"{_rate((t.get('input') or 0) / span)} across the window")
    else:
        head.append(f"{t['needing_action']:,} needing action")
    head.append(f"{t['excess_tokens']:,} above {th['occupancy_warn']:,}")
    lines.append(" · ".join(head))
    lines.append("")
    lines.append("| Session | Project | Kind | Turns | Avg ctx | Peak | Burn | Subs | "
                 "Open for | Idle | Resets | Excess | Do this |")
    lines.append("|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|")
    for r in s["sessions"][:15]:
        # All-time, the table drops the healthy rows: it exists to name what to
        # act on, and a hundred well-behaved sessions bury the two that matter.
        # Inside a named window the question is the other one — what was spent
        # between these two times — and dropping a session that spent tokens
        # would leave a table that disagrees with its own header.
        if not win and r["excess_tokens"] <= 0 and r["severity"] == "ok":
            continue
        subs = (f"{r.get('subagents') or 0}"
                + (f" ({_tok(r['subagent_input'])})" if r.get("subagents") else ""))
        lines.append(
            f"| `{_label(r['transcript'])}` | {r['project'] or '—'} | "
            f"{r['kind']} | {r['turns']:,} | "
            f"{r['avg_context']:,} | {r['peak_context']:,} | "
            f"{_rate(r.get('burn_per_min'))} | {subs} | {_hrs(r['span_hours'])} | "
            f"{_hrs(r['idle_hours'])} | {r['resets']} | {r['excess_tokens']:,} | "
            f"{r['action']} |")

    lines += _hygiene_weight_block(s)

    if t["never_reset"]:
        lines.append("")
        lines.append(f"{t['never_reset']:,} of {t['sessions']:,} session(s) were never reset "
                     "once. A session that grew large and reset repeatedly was busy; one that "
                     "grew large and never reset was unmanaged.")

    drift = [f for f in s["findings"] if f["kind"] == "unmanaged-drift"]
    if drift:
        worst = max(drift, key=lambda f: f["excess_tokens"])
        lines.append("")
        lines.append(f"**Worst drift**: `{worst['session']}` climbed "
                     f"{worst['first_context']:,} → {worst['peak_context']:,} without a single "
                     f"reset, carrying {worst['excess_tokens']:,} tokens above the threshold.")

    lines.append("")
    lines.append(f"_`Excess` is the tokens a session carried ABOVE {th['occupancy_warn']:,}, "
                 "summed over its turns: had it been reset at that line, each of those turns "
                 "would have carried at most the line. Unlike a savings estimate this needs no "
                 "counterfactual — it only counts what was carried over a threshold the session "
                 "could have held._")
    lines.append("")
    lines.append(f"_Age and idleness are not applied to headless runs: an exited run holds no "
                 f"window, so there is nothing to close. Long-lived is {th['age_long_lived_hours']:.0f}h+, "
                 f"stale is {th['idle_stale_hours']:.0f}h+ idle still holding "
                 f"{th['stale_min_context']:,}+._")
    return "\n".join(lines)


def format_surface(s: dict) -> str:
    """Show what is in the window before the conversation starts.

    Grouped by CONNECTOR, because that is the unit you can switch off: a server
    contributes all of its tools or none. Tools sit underneath, so a connector
    with forty unused tools reads as one line rather than forty.
    """
    scope = f" — project `{s['project']}`" if s.get("project") else ""
    t = s["totals"]
    th = s["thresholds"]
    lines = [f"### TokenDog context surface{scope}", ""] + _window_lines(s)
    lines.append(f"{t['connectors']} connector(s) · {t['unused']} never called · "
                 f"{t['low_use']} rarely called · measured over {t['turns']:,} turns "
                 f"in {t['projects']} project(s)")
    lines.append("")

    if not t["have_inventory"]:
        lines.append("_No tool inventory captured, so schema sizes are unknown and the token "
                     "columns are blank. Usage and residency below are exact; run "
                     "`tokendog surface --refresh` to add sizes._")
        lines.append("")

    lines.append("| Connector | Scope | On | Tools used/ships | Calls | Turns resident | "
                 "Calls/1k turns | Schema | Carried | Verdict |")
    lines.append("|---|---|:-:|--:|--:|--:|--:|--:|--:|---|")
    for c in s["connectors"]:
        sch = f"{c['schema_tokens']:,}" if c["schema_tokens"] else "—"
        car = f"{c['carried_tokens']:,}" if c["carried_tokens"] else "—"
        per = f"{c['calls_per_1k_turns']}" if c["calls_per_1k_turns"] is not None else "—"
        of = f"{c['tools_called']}/{c['tools_known']}" if c["tools_known"] else str(c["tools_called"])
        lines.append(f"| `{c['connector']}` | {c['scope']} | {'yes' if c['enabled'] else 'no'} | "
                     f"{of} | {c['calls']:,} | {c['turns_resident']:,} | {per} | "
                     f"{sch} | {car} | {c['action']} |")

    unused = [c for c in s["connectors"] if c["severity"] == "unused" and c["enabled"]]
    if unused:
        lines.append("")
        lines.append("**Disable candidates** — enabled, resident on every turn, never called once:")
        for c in unused:
            where = "everywhere" if c["scope"] == "global" else ", ".join(c["projects"]) or c["scope"]
            carried = (f", carrying {c['carried_tokens']:,} tokens"
                       if c["carried_tokens"] else "")
            note = ""
            if c["schema_tokens"] is None and c.get("probeable"):
                note = " — and it could not be started when probed, so it is pure cost"
            lines.append(f"- `{c['connector']}` — enabled {where}, resident for "
                         f"{c['turns_resident']:,} turns{carried}{note}")

    bloated = [c for c in s["connectors"]
               if c["calls"] and c.get("tools_idle") and len(c["tools_idle"]) > 2]
    if bloated:
        lines.append("")
        lines.append("**Used, but carrying idle tools** — every tool a connector ships is "
                     "resident whether or not it is called:")
        for c in bloated:
            idle = c["tools_idle"]
            shown = ", ".join(f"`{t}`" for t in idle[:6])
            more = f" (+{len(idle) - 6} more)" if len(idle) > 6 else ""
            lines.append(f"- `{c['connector']}` — {len(idle)} of {c['tools_known']} tools "
                         f"never called: {shown}{more}")

    used = [c for c in s["connectors"] if c["calls"]]
    if used:
        lines.append("")
        lines.append("**Tools actually called**, by connector:")
        for c in used:
            tools = ", ".join(f"`{x['tool']}` ×{x['calls']:,}" for x in c["tools"][:8])
            more = f" (+{len(c['tools']) - 8} more)" if len(c["tools"]) > 8 else ""
            lines.append(f"- `{c['connector']}`: {tools}{more}")

    loc = s["local"]
    if loc["skills"] or loc["instructions"] or loc["commands"]:
        lines.append("")
        lines.append(f"**Always-on local surface** — {t['always_on_local_tokens']:,} tokens in "
                     "every prompt:")
        for i in loc["instructions"]:
            lines.append(f"- `{i['name']}` — {i['tokens']:,} tokens")
        for sk in sorted(loc["skills"], key=lambda r: -r["always_on_tokens"]):
            lines.append(f"- skill `{sk['name']}` — {sk['always_on_tokens']:,} always-on, "
                         f"{sk['on_demand_tokens']:,} more when invoked")
        if loc["commands"]:
            tot = sum(c["tokens"] for c in loc["commands"])
            lines.append(f"- {len(loc['commands'])} plugin command(s) — {tot:,} tokens on demand")

    if s["builtin_tools"]:
        top = ", ".join(f"`{b['tool']}` ×{b['calls']:,}" for b in s["builtin_tools"][:6])
        lines.append("")
        lines.append(f"Built-in tools for comparison: {top}.")

    lines.append("")
    lines.append(f"_A connector is resident on every turn of every session where it is enabled, "
                 f"whether or not it is called — so an unused one is a fixed tax on the whole "
                 f"corpus, and the cheapest saving available. Under "
                 f"{th['low_use_per_1k_turns']} call per 1,000 resident turns reads as rarely used._")
    lines.append("")
    lines.append("_Residency is approximate: nothing records which connectors a PAST session had, "
                 "so a connector is credited with the turns of the projects it is configured for. "
                 "Correct for a stable config, an over-estimate for one enabled recently. Usage and "
                 "enablement are exact._")
    return "\n".join(lines)


def format_session(d: dict) -> str:
    """Turn-by-turn: what one session's window was made of.

    The other reports rank sessions; this explains one. "285 turns, 74.9M" says
    a session was expensive and nothing about which reads made it so.
    """
    if not d.get("found"):
        return f"### TokenDog session `{d['session']}`\n\n_{d.get('error', 'not found')}_"
    t = d["totals"]
    b = d["baseline"]
    kind = "headless" if d["headless"] else "interactive"
    lines = [f"### TokenDog session `{d['session']}` — {d.get('project') or '?'} ({kind})",
             ""] + _window_lines(d)
    lines.append(f"{t['turns']:,} turns · {t['input']:,} input · {t['output']:,} output · "
                 f"peak {t['peak_context']:,} · {len(d['segments'])} context(s)"
                 + (f" · {_rate(t.get('burn_per_min'))}" if t.get("burn_per_min") else ""))
    w = d.get("window_totals")
    if w:
        # The window's own figures, kept separate from the session's. Every
        # per-turn cost above is measured against the whole session on purpose
        # — see `session_detail` — so collapsing the two would misreport both.
        lines.append(f"**In window:** {w['turns']:,} turns · {_tok(w['input'])} input · "
                     f"{_rate(w.get('burn_per_min'))} · peak {w['peak_context']:,}"
                     + (f" · {w['first'][11:16]}→{w['last'][11:16]}" if w["first"] else ""))
    lines.append("")

    lines.append("**The floor** — carried by every turn before anything is typed:")
    lines.append("")
    lines.append("| Part | Tokens |")
    lines.append("|---|--:|")
    if b["tool_schemas"]:
        lines.append(f"| Tool schemas (all enabled connectors) | {b['tool_schemas']:,} |")
    lines.append(f"| Skills, always-on | {b['skills_always_on']:,} |")
    lines.append(f"| Instruction files | {b['instruction_files']:,} |")
    lines.append(f"| Remainder — {b['remainder_note']} | {b['remainder']:,} |")
    lines.append(f"| **First turn total** | **{b['total']:,}** |")
    if not b["have_inventory"]:
        lines.append("")
        lines.append("_No tool inventory captured, so schema size is inside the remainder. "
                     "Run `tokendog surface --refresh` to separate it._")

    if len(d["segments"]) > 1:
        lines.append("")
        lines.append("**Contexts** — a reset starts a fresh one inside the same file:")
        lines.append("")
        lines.append("| # | Turns | First → peak → last | Hours |")
        lines.append("|--:|--:|--:|--:|")
        for sg in d["segments"]:
            lines.append(f"| {sg['number']} | {sg['turns']:,} | {sg['first_context']:,} → "
                         f"{sg['peak_context']:,} → {sg['last_context']:,} | {sg['hours']} |")

    lines.append("")
    lines.append("**Costliest additions** — size × the turns that then re-read it:")
    lines.append("")
    lines.append("| Turn | Time | Added | Re-read by | Carried | What arrived |")
    lines.append("|--:|---|--:|--:|--:|---|")
    for tn in d["top_turns"]:
        what = ", ".join(f"`{x['name']}`" + (f" {x['detail'][:46]}" if x["detail"] else "")
                         for x in tn["tools"]) or "—"
        lines.append(f"| {tn['index']} | {tn['at'][5:16]} | {tn['delta']:,} | "
                     f"{tn['turns_after']:,} | {tn['carried']:,} | {what} |")

    if d["by_tool"]:
        lines.append("")
        lines.append("**By tool**, across the whole session:")
        lines.append("")
        lines.append("| Tool | Calls | Result tokens | Carried |")
        lines.append("|---|--:|--:|--:|")
        for r in d["by_tool"][:12]:
            lines.append(f"| `{r['tool']}` | {r['calls']:,} | {r['tokens']:,} | {r['carried']:,} |")

    lines.append("")
    lines.append(f"_Residual across all turns: {t['residual']:,} tokens — growth not attributable "
                 "to a tool result or the previous reply (a typed message, an injected reminder). "
                 "Reported rather than assigned to whichever category is nearest._")
    lines.append("")
    lines.append("_`Carried` is what an addition cost after it arrived: its size times the turns "
                 "that re-read it before the next reset. Counting past a reset would charge a read "
                 "for turns that never saw it._")
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


# The reports whose figures are per-turn, and so can be scoped to a time range.
# `cost` is absent on purpose: its --since/--until are dates for the SQL
# roll-up, a different (and older) meaning of the same two flag names.
WINDOWED_COMMANDS = ("bands", "resumes", "coldstart", "hygiene", "surface", "session", "outcomes", "errors", "pipelines", "discovery", "floor")


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

    ef = sub.add_parser("effect")
    ef.add_argument("--split", help="YYYY-MM-DD: compare before vs on/after this date")
    ef.add_argument("--project")

    bd = sub.add_parser("bands")
    bd.add_argument("--project")

    rs = sub.add_parser("resumes", help="windows carried past the moment to reset")
    rs.add_argument("--project")

    cs = sub.add_parser("coldstart", help="discovery paid for more than once")
    cs.add_argument("--project")

    hy = sub.add_parser("hygiene", help="which sessions were managed, and what drift cost")
    hy.add_argument("--project")

    se = sub.add_parser("session", help="turn-by-turn drilldown of one session")
    se.add_argument("id", help="session/transcript id, or any unique prefix")
    se.add_argument("--top", type=int, default=15)
    se.add_argument("--json", action="store_true", dest="as_json")

    sf = sub.add_parser("surface", help="what is in the window before the conversation starts")
    sf.add_argument("--project")
    sf.add_argument("--refresh", action="store_true",
                    help="start each enabled connector to measure its tool schemas, and cache "
                         "the result. The only tokendog command that launches anything")
    sf.add_argument("--timeout", type=float, default=None,
                    help="seconds to wait per connector when refreshing")
    sf.add_argument("--json", action="store_true", help="emit the raw view-model")
    sf.add_argument("--disable", metavar="CONNECTOR",
                    help="turn a connector off (dry run unless --apply)")
    sf.add_argument("--enable", metavar="CONNECTOR",
                    help="turn a previously disabled connector back on")
    sf.add_argument("--apply", action="store_true",
                    help="actually make the edit, after backing the file up")
    sf.add_argument("--force", action="store_true",
                    help="disable even a connector with recorded calls")

    sv = sub.add_parser("serve", help="local dashboard over the same figures")
    sv.add_argument("--port", type=int, default=None)
    sv.add_argument("--address", default=None,
                    help="interface to bind (default loopback; anything else is "
                         "unauthenticated and shows project names and token counts)")
    sv.add_argument("--project")
    sv.add_argument("--quiet", action="store_true", help="suppress per-request logging")

    # Every report that measures turns takes the same window. Added in one loop
    # so a new windowed report cannot be added with a differently-spelled flag.
    er = sub.add_parser("errors", help="which tools/MCP servers fail most, and how")
    er.add_argument("--project")
    pl = sub.add_parser("pipelines", help="Claude spend per Glitch pipeline (reads .glitch/runs)")
    pl.add_argument("--project")
    dv = sub.add_parser("discovery", help="how much of each session was finding vs doing (grep-loop flag)")
    dv.add_argument("--project")
    fl = sub.add_parser("floor", help="context floor budget: MCPs/skills/instructions, sized + used-or-not")
    fl.add_argument("--project")
    oc = sub.add_parser("outcomes", help="cost per merged PR — links sessions to commits to PRs")
    oc.add_argument("--project")
    oc.add_argument("--gh", action="store_true",
                    help="resolve PR numbers via the GitHub CLI when the local history has no "
                         "merge commit (squash-merge). Uses gh; GitHub rate limit, not Claude tokens")
    oc.add_argument("--all-authors", action="store_true",
                    help="attribute commits by anyone, not just this machine's git email "
                         "(default is owner-only, to avoid mis-linking a teammate's commit)")

    for _p in (bd, rs, cs, hy, sf, se, oc, er, pl, dv, fl):
        _p.add_argument("--since", metavar="WHEN",
                        help="only turns at or after this time: 17:29, 2026-09-08, "
                             "2026-09-08T17:29, or a span back from now like 90m / 3h / 2d")
        _p.add_argument("--until", metavar="WHEN",
                        help="only turns before this time; same forms as --since")

    ih = sub.add_parser("install-hook",
                        help="stamp the Claude session id into commits (exact PR attribution)")
    ih.add_argument("--target", default=".", help="repo to install into (default: cwd)")
    ih.add_argument("--remove", action="store_true", help="remove the hook tokendog installed")
    ih.add_argument("--status", action="store_true", help="show what is installed, change nothing")
    ih.add_argument("--force", action="store_true", help="append to an existing foreign hook")

    i = sub.add_parser("init")
    i.add_argument("--target", default=".")
    i.add_argument("--force", action="store_true")

    args = p.parse_args(argv)

    # One window, resolved once, for the reports that take turn-level ranges.
    # `cost` is deliberately not among them: its --since/--until are dates
    # handed to the SQL roll-up, and reinterpreting them here would change the
    # meaning of a flag that already worked.
    win = None
    if args.cmd in WINDOWED_COMMANDS:
        from .window import WindowError, resolve
        try:
            win = resolve(args.since, args.until)
        except WindowError as exc:
            print(f"tokendog {args.cmd}: {exc}")
            return 2

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
    elif args.cmd == "effect":
        print(format_effect(effect_data(split=args.split, project=args.project)))
    elif args.cmd == "savings":
        from .savings import savings_summary
        print(format_savings(savings_summary()))
    elif args.cmd == "bands":
        print(format_bands(band_report_data(project=args.project, window=win)))
    elif args.cmd == "resumes":
        from .limit_resume import resume_report_data
        print(format_resumes(resume_report_data(project=args.project, window=win)))
    elif args.cmd == "coldstart":
        from .cold_start import cold_start_report_data
        print(format_cold_start(cold_start_report_data(project=args.project, window=win)))
    elif args.cmd == "hygiene":
        from .hygiene import hygiene_report_data
        print(format_hygiene(hygiene_report_data(project=args.project, window=win)))
    elif args.cmd == "errors":
        from .errors import tool_errors
        print(format_errors(tool_errors(project=args.project, window=win)))
    elif args.cmd == "pipelines":
        from .glitch_runs import pipeline_costs
        print(format_pipelines(pipeline_costs(project=args.project, window=win)))
    elif args.cmd == "discovery":
        from .discovery import discovery_report
        print(format_discovery(discovery_report(project=args.project, window=win)))
    elif args.cmd == "floor":
        from .floor import floor_budget
        print(format_floor(floor_budget(project=args.project, window=win)))
    elif args.cmd == "outcomes":
        from .outcomes import outcomes_data
        print(format_outcomes(outcomes_data(project=args.project, window=win,
                                            use_gh=getattr(args, "gh", False),
                                            own_author_only=not getattr(args, "all_authors", False))))
    elif args.cmd == "session":
        from .session_detail import session_detail
        data = session_detail(args.id, top=args.top, window=win)
        print(json.dumps(data, indent=1) if args.as_json else format_session(data))
    elif args.cmd == "surface":
        from .surface import probe_configs, save_inventory, surface_report_data
        if args.disable or args.enable:
            from .disable import apply as apply_change, format_plan, plan
            name = args.disable or args.enable
            turning_on = bool(args.enable)
            if args.disable and not args.force:
                data = surface_report_data()
                row = next((c for c in data["connectors"]
                            if c["connector"] == name), None)
                if row and row["calls"]:
                    print(f"`{name}` has {row['calls']:,} recorded call(s) "
                          f"({row['calls_per_1k_turns']} per 1k turns). Refusing to disable "
                          "something in use — pass --force if that is what you mean.")
                    return 1
            if args.apply:
                print(format_plan(apply_change(name, enable=turning_on), applied=True))
            else:
                print(format_plan(plan(name, enable=turning_on)))
            return 0
        if args.refresh:
            from .surface_probe import DEFAULT_TIMEOUT, format_probe, probe_connectors
            configs = probe_configs()
            if not configs:
                print("No connector has a launch config to probe.")
            else:
                print(f"Starting {len(configs)} connector(s) to read their tool lists. "
                      "This is the one command that launches anything; a server may prompt "
                      "for credentials.")
                sizes, results = probe_connectors(
                    configs, timeout=args.timeout or DEFAULT_TIMEOUT)
                print(format_probe(results))
                path = save_inventory(sizes)
                print(f"Cached {len(sizes)} tool size(s) to {path}")
                print()
        data = surface_report_data(project=args.project, window=win)
        if args.json:
            print(json.dumps(data, indent=1))
        else:
            print(format_surface(data))
    elif args.cmd == "serve":
        from .server import serve, DEFAULT_PORT, DEFAULT_ADDRESS
        return serve(port=args.port or DEFAULT_PORT,
                     address=args.address or DEFAULT_ADDRESS,
                     project=args.project, quiet=args.quiet)
    elif args.cmd == "install-hook":
        from .hooks import install, remove, hook_status, format_install
        if args.status:
            st = hook_status(args.target)
            print(f"{args.target}: " + ("installed" if st["installed"]
                  else "foreign hook present" if st["foreign"]
                  else "not installed" if st["is_repo"] else "not a git repo")
                  + (f" ({st['path']})" if st.get("path") else ""))
        elif args.remove:
            print(format_install(remove(args.target)))
        else:
            print(format_install(install(args.target, force=args.force)))
    elif args.cmd == "init":
        written = apply_templates(repo_templates_dir(), args.target, force=args.force)
        for p in written:
            print(f"wrote {p}")
        print("Edit CLAUDE.md below the TOKENDOG_EXTENSION_MARKER for org-specific instructions.")
        print("A statusLine was added to .claude/settings.json — context occupancy, session age "
              "and the 5h/7d limits, on screen while you work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
