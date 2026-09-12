# How TokenDog Works

## The money

Every metered turn is billed in four buckets: fresh input, output, cache read (0.1× input) and
cache write (1.25× input for the 5-minute TTL, 2× for the 1-hour). TokenDog prices all four from
the authoritative `usage` block in each Claude Code transcript turn; hook events carry only a
tiktoken estimate of tool-payload *volume* and are never priced.

Measured on one developer's 19,466 turns, the bill was roughly 62% cache read, 29% cache write,
9% output and 0% fresh input. So there are three levers, in that order:

1. **Carry less.** Every token in the window is re-read on every later turn. Half the turns ran at
   200K+ context and carried 84% of all context tokens. Closing idle sessions, compacting, and
   not resuming a full window past a limit are the biggest wins, and the dashboard's act-now list
   is built around them.
2. **Rewrite less.** A prefix that churns is re-written to the cache at 1.25–2× input. Stable
   system prompts and connector schemas keep the prefix cacheable.
3. **Generate less.** Output is the last ninth. Model routing (Sonnet measured −40% on one
   project) matters more than terseness.

## What is measured, what is opt-in, and what did not help

- **Measurement is on by default** and reads only local files: the transcripts under
  `~/.claude/projects`, the hook sink under `~/.tokendog`. Nothing leaves the machine.
- **The tool-output condenser is off by default.** `shadow` logs what it would have saved without
  touching output; `enforce` applies it. Replay on real transcripts showed the large payloads on a
  coding workload are files the model asked to read, which the condenser now never cuts — leaving a
  measured saving of ~0.03% of input tokens per day. Run `tokendog condense --replay` on your own
  history before enabling anything.
- **The frugal skill** produced no measurable change in tool-payload volume (+0.1%).
- **The Rust gate** is experimental and unwired. On a cache-dominated workload its transforms
  invalidate the prefix and cost more than they save; see `tokendog-gate/README.md`.

## The framework map

The 47 items below are the full taxonomy TokenDog was designed against, in ten categories A–J.
The ordering is a taxonomy, not a priority: C (caching) and the context-size items in B are where
the money is; E (output) governs about a ninth of it.

**Status key:** **Shipped** — live with tests. **Opt-in** — shipped, off by default. **Experimental**
— code exists in the gate but nothing calls it. **Not built.**

---

## A · Fixed-prompt shrink

Items paid on every API call and prefix-cached; reducing them gives compounding savings.

| # | Item | Module | Status |
|---|---|---|---|
| 1 | Skill diet — demote always-on skills to model-invoked where "always" isn't load-bearing | `tokendog-frugal/SKILL.md` | Shipped |
| 2 | Trim SKILL.md bodies; move deep refs to companion files loaded on demand | `tokendog-frugal/SKILL.md` (lean by design) | Shipped |
| 3 | Progressive skill disclosure — thin SKILL.md + on-demand `prompts/*.md` per phase | `tokendog-frugal/SKILL.md` demonstrates pattern | Shipped |
| 4 | Per-subagent tool allowlisting — trim `tools:` frontmatter to actual need | `settings.json` template; frugal skill teaches | Shipped |
| 5 | MCP tool deferred loading — server marks tools deferred so schemas load on demand via ToolSearch | `tokendog_mcp/deferred.py` | Shipped |
| 6 | Trim CLAUDE.md — terse project instructions, no marketing prose | `CLAUDE.md` template | Shipped |
| 7 | Structured tool schemas — dense JSON over verbose prose in tool descriptions | `tokendog_mcp/schema.py` | Shipped |

---

## B · Variable-prompt shrink

Tool responses are the largest cost surface (~60–80% of input tokens on coding workloads). These
items shrink what enters the context window from each tool call.

| # | Item | Module | Status |
|---|---|---|---|
| 8 | Bake into always-on skill: `Read` offset+limit; narrow `Grep`; never dump full dirs | `tokendog-frugal/SKILL.md` + `CLAUDE.md` template | Shipped |
| 9 | MCP server-side pagination + response caps | `tokendog_mcp/pagination.py` | Shipped |
| 10 | Tool-output condenser — keep matches/failures/head+tail of command output; never cut a file read | `scripts/truncate_output.py` + `tokendog.condense` | Opt-in |
| 11 | JSON tool-response compression (SmartCrusher-inspired pattern, native impl) | Gate: `tokendog-gate/compression/smart_crusher` | Experimental |
| 12 | Reversible compression + retrieve tool (CCR-inspired pattern, native impl) | Gate: `tokendog-gate/compression/ccr` | Experimental |
| 13 | Context deduplication — don't inject same file/MCP result twice in a session | Gate: `tokendog-gate/context/dedup` | Experimental |
| 14 | Response truncation heuristics — head/tail slice, drop middle if not code | Plugin: `tokendog.truncate` (shipped); Gate: server variant deferred | Opt-in (plugin); Experimental |
| 15 | Batch MCP queries — bundle N related lookups into one call | `tokendog_mcp/batch.py` | Shipped |
| 16 | Client-side MCP response cache (session-scoped) | Plugin or Gate | Not built |

---

## C · Prompt caching

Anthropic's 5-minute KV-cache TTL gives a ~90% discount on repeated stable prefixes. These items
maximize cache hit rate.

| # | Item | Module | Status |
|---|---|---|---|
| 17 | Prefix stability — put stable content at prompt top | Guide in this doc; Gate enforces | Experimental |
| 18 | Deterministic ordering — sort tools/skills/JSON keys byte-identically | Gate: `tokendog-gate/caching/deterministic` | Experimental |
| 19 | Explicit `cache_control` markers via SDK | Gate: `tokendog-gate/caching/cache_control` | Experimental |
| 20 | `prompt_cache_key` pooling — key = team; N devs share cache within 5-min window | Gate: `tokendog-gate/caching/pool` | Experimental |
| 21 | Session-cross-restart persistence — serialize conversation so cache survives Cmd+Q | Gate: `tokendog-gate/caching/session` | Experimental |

---

## D · Smart routing

Static, config-based routing decisions — no LLM triage (which would double cost). Every routing
decision is declarative.

| # | Item | Module | Status |
|---|---|---|---|
| 22 | Model tier per subagent / command — static, config-based | `settings.json` template; per-agent frontmatter | Shipped |
| 23 | Account-level default → Haiku for chat, Sonnet for code, Opus opt-in | `settings.json` template | Shipped |
| 24 | Rules-based intent routing (declarative heuristics, no extra LLM call) | Gate: `tokendog-gate/routing/rules` | Experimental |
| 25 | Deterministic scripts replace trivial Claude calls (lint, format, secret-scan, complexity) | `scripts/` hook scripts | Shipped |
| 26 | Self-hosted / fine-tuned model for high-volume repetitive tasks | Gate: extension backend trait | Experimental |
| 27 | Local Ollama-tier model for classification / summarization | Gate: `tokendog-gate/routing/local_model` | Experimental |
| 28 | Semantic Q→A cache — identical/near-identical questions return cached answers | Gate: `tokendog-gate/context/semantic_cache` | Experimental |

---

## E · Output-token discipline

Output tokens cost ~5× input tokens at Anthropic pricing. Keeping outputs tight matters.

| # | Item | Module | Status |
|---|---|---|---|
| 29 | Enforce brevity in subagent + skill prompts ("under N words", "no summary") | `tokendog-frugal/SKILL.md` | Shipped |
| 30 | Structured outputs (JSON schema) for subagent returns — no prose overhead | `tokendog-frugal/SKILL.md`; per-agent frontmatter pattern | Shipped |
| 31 | Stream-and-truncate — cancel completion early on drift signals | Gate: `tokendog-gate/routing/stream_abort` | Experimental |

---

## F · Observability + budgets

Measure first; optimize second. Items 32 and 33 are prerequisites for tuning any other item.

| # | Item | Module | Status |
|---|---|---|---|
| 32 | Token telemetry hook — local tiktoken approximation on Stop/PreToolUse (Path A) | `tokendog.approx` + `scripts/token_count.py` | Shipped |
| 33 | Token telemetry via gateway — real `usage.*` from API response body (Path C) | Gate: `tokendog-gate/telemetry/usage` | Experimental |
| 34 | Cost analytics MCP — reads telemetry stream, returns per-user/team/workflow rollups | `mcpServers/tokendog-cost/` | Shipped |
| 35 | `/tokendog:cost` slash command — per-runtime/tool cost breakdown | `commands/tokendog-cost.md` | Shipped |
| 36 | Per-user / per-team hard budgets | `scripts/budget_enforce.py` + `tokendog.budget` | Shipped |
| 37 | Slack/webhook alerts when session exceeds threshold | `scripts/budget_alert.py` | Shipped |

Beyond the original 47, an analysis + forensics layer was added on top of the same measurement
spine — all deterministic, transcript-only, no API key or LLM call:

| Report | Module | What it answers |
|---|---|---|
| `bands` | `bands.py` | Turns grouped by context occupancy — where the tokens actually are |
| `hygiene` | `hygiene.py` | Was a session managed? Burn rate, resets, subagent roll-up, and the `excess` it carried above a threshold |
| `resumes` | `limit_resume.py` | A large window carried past an obvious reset point |
| `coldstart` | `cold_start.py` | Discovery paid for more than once across headless runs |
| `surface` | `surface.py` | What every prompt carries before you type; `--disable` turns a connector off (reversible) |
| `session <id>` | `session_detail.py` | Turn-by-turn: what entered the window each turn and what it then cost |
| `errors` | `errors.py` | Per-tool error rate + dominant failure category (pattern-matched) |
| `outcomes` / `pipelines` | `outcomes.py`, `glitch_runs.py`, `hooks.py` | Cost per merged PR / per Glitch pipeline; a `prepare-commit-msg` hook stamps the session id for EXACT links |
| time windows | `window.py` | `--since`/`--until` on every report above — local clock times, dates, spans; scopes turns, not a session's age |
| dashboard | `server.py`, `static/` | All of the above as one loopback page, colourblind-safe, one global range control |

---

## G · Org-wide defaults

Config changes that affect every developer in the org automatically, with zero per-dev effort.

| # | Item | Module | Status |
|---|---|---|---|
| 38 | Canonical CLAUDE.md template — devs drop into their repos | `CLAUDE.md.template` + `tokendog init` | Shipped |
| 39 | Canonical `.claude/settings.json` — permission/tool allowlist, telemetry sink, model default | `settings.json.template` + `tokendog init` | Shipped |
| 40 | Endorsed MCP set — "these are the MCPs to keep; disable rest by default" | `mcp-hygiene.md`; `/tokendog:doctor` scans | Shipped |
| 41 | Frugal-prompting always-on skill — teaches Claude to be frugal regardless of dev's ask | `tokendog-frugal/SKILL.md` | Shipped |

---

## H · Behavioral / cultural

Norms and habits that reduce token burn without any code change. Delivered via always-on skills and
targeted reminder hooks.

| # | Item | Module | Status |
|---|---|---|---|
| 42 | Session hygiene training + docs — `/clear` on topic switch, don't paste 5000-line logs | `tokendog-hygiene/SKILL.md` + this doc | Shipped |
| 43 | Batch questions instead of chatty back-and-forth | `tokendog-hygiene/SKILL.md` | Shipped |
| 44 | Reminder hook on Stop if session used > threshold | `scripts/session_summary.py` | Shipped |

---

## I · Session lifecycle

Manage session boundaries to prevent runaway context accumulation.

| # | Item | Module | Status |
|---|---|---|---|
| 45 | Idle-session cleanup — auto-close after N minutes idle | Plugin hook (idle_cleanup) | Not built |
| 46 | Conversation compaction tuning — thresholds + preserve strategy | `settings.json` template (`compactionThreshold`) | Shipped |

---

## J · Cross-session memory

Persist facts across sessions without re-prompting, eliminating repeated context injection.

| # | Item | Module | Status |
|---|---|---|---|
| 47 | Anthropic Memory API — persistent facts survive across sessions without re-prompting | Plugin + Gate integration | Not built |

---

## Summary

| Category | Items | Shipped (Slices 1–4) | Experimental | Not built |
|---|---|---|---|---|
| A · Fixed-prompt shrink | 1–7 | 7 | — | — |
| B · Variable-prompt shrink | 8–16 | 5 (8–10, 14, 15) | 4 (11–13, 14 gate) | 1 (16) |
| C · Prompt caching | 17–21 | — | 5 | — |
| D · Smart routing | 22–28 | 3 (22, 23, 25) | 4 (24, 26–28) | — |
| E · Output-token discipline | 29–31 | 2 (29, 30) | 1 | — |
| F · Observability + budgets | 32–37 | 5 (32, 34–37) | 1 (33) | — |
| G · Org-wide defaults | 38–41 | 4 | — | — |
| H · Behavioral / cultural | 42–44 | 3 | — | — |
| I · Session lifecycle | 45–46 | 1 (46) | — | 1 (45) |
| J · Cross-session memory | 47 | — | — | 1 |

Counted by status: 30 shipped, 2 opt-in, 12 experimental in the gate, 3 not built. The experimental
items are the transport-layer ones, and the measurement above is why they are not wired.
