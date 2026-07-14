# How TokenDog Works — The 47-Item Framework

TokenDog implements a full-stack token-optimization framework covering every layer of Claude Code
and Glitch spend: fixed prompts, variable tool outputs, prompt caching, model routing, output
discipline, observability, org-wide defaults, behavioral norms, session lifecycle, and cross-session
memory. The 47 items below are the exhaustive list, grouped into ten categories A–J.

**Status key:**
- **Shipped (Slice N)** — live in this repo with tests; module listed.
- **Gate (Slice 6+)** — requires the TokenDog transport gateway; deferred pending release approval.
- **Deferred** — planned post-Slice 4; no gateway required but not yet built.

---

## A · Fixed-prompt shrink

Items paid on every API call and prefix-cached; reducing them gives compounding savings.

| # | Item | Module | Status |
|---|---|---|---|
| 1 | Skill diet — demote always-on skills to model-invoked where "always" isn't load-bearing | `tokendog-frugal/SKILL.md` | Shipped (Slice 2) |
| 2 | Trim SKILL.md bodies; move deep refs to companion files loaded on demand | `tokendog-frugal/SKILL.md` (lean by design) | Shipped (Slice 2) |
| 3 | Progressive skill disclosure — thin SKILL.md + on-demand `prompts/*.md` per phase | `tokendog-frugal/SKILL.md` demonstrates pattern | Shipped (Slice 2) |
| 4 | Per-subagent tool allowlisting — trim `tools:` frontmatter to actual need | `settings.json` template; frugal skill teaches | Shipped (Slice 2) |
| 5 | MCP tool deferred loading — server marks tools deferred so schemas load on demand via ToolSearch | `tokendog_mcp/deferred.py` | Shipped (Slice 4) |
| 6 | Trim CLAUDE.md — terse project instructions, no marketing prose | `CLAUDE.md` template | Shipped (Slice 3) |
| 7 | Structured tool schemas — dense JSON over verbose prose in tool descriptions | `tokendog_mcp/schema.py` | Shipped (Slice 4) |

---

## B · Variable-prompt shrink

Tool responses are the largest cost surface (~60–80% of input tokens on coding workloads). These
items shrink what enters the context window from each tool call.

| # | Item | Module | Status |
|---|---|---|---|
| 8 | Bake into always-on skill: `Read` offset+limit; narrow `Grep`; never dump full dirs | `tokendog-frugal/SKILL.md` + `CLAUDE.md` template | Shipped (Slice 2) |
| 9 | MCP server-side pagination + response caps | `tokendog_mcp/pagination.py` | Shipped (Slice 4) |
| 10 | Bash-output capping — pipe long outputs through `head`; hook truncates results > N lines | `scripts/truncate_output.py` + `tokendog.truncate` | Shipped (Slice 1/2) |
| 11 | JSON tool-response compression (SmartCrusher-inspired pattern, native impl) | Gate: `compact-gate/compression/smart_crusher` | Gate (Slice 6+) |
| 12 | Reversible compression + retrieve tool (CCR-inspired pattern, native impl) | Gate: `compact-gate/compression/ccr` | Gate (Slice 6+) |
| 13 | Context deduplication — don't inject same file/MCP result twice in a session | Gate: `compact-gate/context/dedup` | Gate (Slice 6+) |
| 14 | Response truncation heuristics — head/tail slice, drop middle if not code | Plugin: `tokendog.truncate` (shipped); Gate: server variant deferred | Shipped plugin (Slice 1); Gate (Slice 6+) |
| 15 | Batch MCP queries — bundle N related lookups into one call | `tokendog_mcp/batch.py` | Shipped (Slice 4) |
| 16 | Client-side MCP response cache (session-scoped) | Plugin or Gate | Deferred |

---

## C · Prompt caching

Anthropic's 5-minute KV-cache TTL gives a ~90% discount on repeated stable prefixes. These items
maximize cache hit rate.

| # | Item | Module | Status |
|---|---|---|---|
| 17 | Prefix stability — put stable content at prompt top | Guide in this doc; Gate enforces | Gate (Slice 6+) |
| 18 | Deterministic ordering — sort tools/skills/JSON keys byte-identically | Gate: `compact-gate/caching/deterministic` | Gate (Slice 6+) |
| 19 | Explicit `cache_control` markers via SDK | Gate: `compact-gate/caching/cache_control` | Gate (Slice 6+) |
| 20 | `prompt_cache_key` pooling — key = team; N devs share cache within 5-min window | Gate: `compact-gate/caching/pool` | Gate (Slice 6+) |
| 21 | Session-cross-restart persistence — serialize conversation so cache survives Cmd+Q | Gate: `compact-gate/caching/session` | Gate (Slice 6+) |

---

## D · Smart routing

Static, config-based routing decisions — no LLM triage (which would double cost). Every routing
decision is declarative.

| # | Item | Module | Status |
|---|---|---|---|
| 22 | Model tier per subagent / command — static, config-based | `settings.json` template; per-agent frontmatter | Shipped (Slice 3) |
| 23 | Account-level default → Haiku for chat, Sonnet for code, Opus opt-in | `settings.json` template | Shipped (Slice 3) |
| 24 | Rules-based intent routing (declarative heuristics, no extra LLM call) | Gate: `compact-gate/routing/rules` | Gate (Slice 6+) |
| 25 | Deterministic scripts replace trivial Claude calls (lint, format, secret-scan, complexity) | `scripts/` hook scripts | Shipped (Slice 2) |
| 26 | Self-hosted / fine-tuned model for high-volume repetitive tasks | Gate: extension backend trait | Gate (Slice 6+) |
| 27 | Local Ollama-tier model for classification / summarization | Gate: `compact-gate/routing/local_model` | Gate (Slice 6+) |
| 28 | Semantic Q→A cache — identical/near-identical questions return cached answers | Gate: `compact-gate/context/semantic_cache` | Gate (Slice 6+) |

---

## E · Output-token discipline

Output tokens cost ~5× input tokens at Anthropic pricing. Keeping outputs tight matters.

| # | Item | Module | Status |
|---|---|---|---|
| 29 | Enforce brevity in subagent + skill prompts ("under N words", "no summary") | `tokendog-frugal/SKILL.md` | Shipped (Slice 2) |
| 30 | Structured outputs (JSON schema) for subagent returns — no prose overhead | `tokendog-frugal/SKILL.md`; per-agent frontmatter pattern | Shipped (Slice 2) |
| 31 | Stream-and-truncate — cancel completion early on drift signals | Gate: `compact-gate/routing/stream_abort` | Gate (Slice 6+) |

---

## F · Observability + budgets

Measure first; optimize second. Items 32 and 33 are prerequisites for tuning any other item.

| # | Item | Module | Status |
|---|---|---|---|
| 32 | Token telemetry hook — local tiktoken approximation on Stop/PreToolUse (Path A) | `tokendog.approx` + `scripts/token_count.py` | Shipped (Slice 1/2) |
| 33 | Token telemetry via gateway — real `usage.*` from API response body (Path C) | Gate: `compact-gate/telemetry/usage` | Gate (Slice 6+) |
| 34 | Cost analytics MCP — reads telemetry stream, returns per-user/team/workflow rollups | `mcpServers/tokendog-cost/` | Shipped (Slice 2) |
| 35 | `/tokendog:cost` slash command — per-runtime/tool cost breakdown | `commands/tokendog-cost.md` | Shipped (Slice 2) |
| 36 | Per-user / per-team hard budgets | `scripts/budget_enforce.py` + `tokendog.budget` | Shipped (Slice 2) |
| 37 | Slack/webhook alerts when session exceeds threshold | `scripts/budget_alert.py` | Shipped (Slice 2) |

---

## G · Org-wide defaults

Config changes that affect every developer in the org automatically, with zero per-dev effort.

| # | Item | Module | Status |
|---|---|---|---|
| 38 | Canonical CLAUDE.md template — devs drop into their repos | `CLAUDE.md.template` + `tokendog init` | Shipped (Slice 3) |
| 39 | Canonical `.claude/settings.json` — permission/tool allowlist, telemetry sink, model default | `settings.json.template` + `tokendog init` | Shipped (Slice 3) |
| 40 | Endorsed MCP set — "these are the MCPs to keep; disable rest by default" | `mcp-hygiene.md`; `/tokendog:doctor` scans | Shipped (Slice 3) |
| 41 | Frugal-prompting always-on skill — teaches Claude to be frugal regardless of dev's ask | `tokendog-frugal/SKILL.md` | Shipped (Slice 2) |

---

## H · Behavioral / cultural

Norms and habits that reduce token burn without any code change. Delivered via always-on skills and
targeted reminder hooks.

| # | Item | Module | Status |
|---|---|---|---|
| 42 | Session hygiene training + docs — `/clear` on topic switch, don't paste 5000-line logs | `tokendog-hygiene/SKILL.md` + this doc | Shipped (Slice 2) |
| 43 | Batch questions instead of chatty back-and-forth | `tokendog-hygiene/SKILL.md` | Shipped (Slice 2) |
| 44 | Reminder hook on Stop if session used > threshold | `scripts/session_summary.py` | Shipped (Slice 2) |

---

## I · Session lifecycle

Manage session boundaries to prevent runaway context accumulation.

| # | Item | Module | Status |
|---|---|---|---|
| 45 | Idle-session cleanup — auto-close after N minutes idle | Plugin hook (idle_cleanup) | Deferred |
| 46 | Conversation compaction tuning — thresholds + preserve strategy | `settings.json` template (`compactionThreshold`) | Shipped (Slice 3) |

---

## J · Cross-session memory

Persist facts across sessions without re-prompting, eliminating repeated context injection.

| # | Item | Module | Status |
|---|---|---|---|
| 47 | Anthropic Memory API — persistent facts survive across sessions without re-prompting | Plugin + Gate integration | Deferred |

---

## Summary

| Category | Items | Shipped (Slices 1–4) | Gate (Slice 6+) | Deferred |
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

**30 of 47 items are shipped in Slices 1–4 and available today.** The gate items (Slices 6+) require
the TokenDog transport proxy and are pending release approval. Deferred items need no proxy but are
not yet built.

The measurement spine (`tokendog.approx`, `tokendog.truncate`) in Slice 1 is the prerequisite for
every observability item. Ship it first; tune everything else from real data.
