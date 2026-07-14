# compact — Claude Code Token Optimization Framework

**Design brief. Self-contained. Written to be picked up by a fresh Claude Code session with zero prior context.**

---

## §0 · How to use this document

You're reading a design brief for **compact**, a full-stack open-source framework for reducing Claude Code or Glitch token consumption at scale for developer organizations.

Read in order:

1. §1 · Problem statement + framing
2. §2 · Positioning vs Headroom (why build this, why not just use Headroom)
3. §3 · Telemetry gap analysis (what's already tracked, what's missing)
4. §4 · The 47-item optimization framework
5. §5 · Product architecture (5 layers, repo structure)
6. §6 · All 47 items mapped to code locations
7. §7 · Extension mechanism (4 extension points)
8. §8 · Phasing plan (day 1 to v1.0)
9. §9 · Comparison vs Headroom
10. §10 · Open decisions
11. §11 · Getting started (what to build first)

If you're implementing right away, skip to §8 and §11. Reference the mapping table (§6) for where each item lives.

---

## §1 · Problem statement

Engineering organizations that adopt Claude Code broadly (hundreds to thousands of developers) burn a large token bill on **day-to-day coding activity**, not just on plugin-specific workflows. The dominant cost is free-form dev usage: `Read` / `Grep` / `Edit` tool loops, `Bash` output slurps, MCP tool responses, and conversation history that accumulates through the day.

Optimizing only "plugin-authored surfaces" (slash commands, subagents) misses ~95% of the actual spend. The goal is org-wide reduction in token cost for **all Claude Code activity**, whether or not the developer touches a specific plugin.

**Scope**: measurable reduction (target 30-60%) in token spend across an org's Claude Code footprint, without loss of output quality.

---

## §2 · Positioning vs Headroom

### What Headroom is

Headroom (`https://github.com/headroom-labs/headroom`, Rust) is a **transport-layer proxy** that sits between the coding agent and the LLM API. It compresses tool outputs, aligns cache prefixes, and optionally uses trained compression models. Deployed as `headroom proxy --port 8787` or `headroom wrap claude`.

Techniques:

- **SmartCrusher** — Statistical JSON compression (array sampling, dict field dropping) — 70-90% savings on tool outputs.
- **CodeCompressor** — AST-aware code compression — disabled by default (40% syntax error rate).
- **Kompress-v2-base** — ONNX ML model for plain text/log compression — ~30% savings.
- **CacheAligner** — Deterministic tool sorting, JSON key ordering, `cache_control` placement, `prompt_cache_key` injection — maximizes KV cache hits.
- **CCR (Compress-Cache-Retrieve)** — Reversible compression with local SQLite storage; LLM calls `headroom_retrieve` tool to fetch originals on demand.

Claimed savings: 60-95% on JSON-heavy workloads, 15-20% on general coding agents.

Known concerns (from their own audit docs, `REALIGNMENT` file):
- Cache-killer bugs (proxy dropping messages to fit budgets, busting Anthropic's KV cache on every compression event)
- Benchmarks likely run post-fix or under conditions that don't trigger cache busts
- Single-vendor project, early-stage stabilization

### Why compact is not "Headroom-lite"

Headroom operates on **one layer** — network transport. compact operates on **five layers**:

| Layer | Purpose | Headroom covers? |
|---|---|---|
| **Plugin** | Always-on frugal skill; cost telemetry hooks; slash commands for cost audit/budget/doctor; deterministic scripts | ❌ |
| **Config templates** | CLAUDE.md and settings.json baselines that teach frugality org-wide | ❌ |
| **MCP toolkit** | Libraries + patterns for MCP authors to build frugal MCPs | ❌ |
| **Transport gateway** | Proxy with compression, cache pooling, budget enforcement, routing | ✅ competing layer |
| **Behavioral / docs** | Session hygiene guides, batching patterns, memory API usage, benchmark suite | ❌ |

Our gateway layer competes with Headroom directly. The other four layers are net-new — no existing product bundles them.

**compact is a superset of Headroom as a product**, not a subset.

### Why not just use Headroom

1. **Single-vendor risk** — early-stage stabilization; recon flagged cache-killer bug in their own audit docs.
2. **License + IP** — depending on their license, embedding may complicate our OSS story.
3. **Design coupling** — building the gateway ourselves lets it co-design with the plugin, telemetry, and budget model. Bolting Headroom in means adapters and impedance mismatch forever.
4. **Governance** — an OSS product needs a single roadmap owner. Two upstream projects = political overhead.

**Recommendation**: build the transport gateway from first principles. Take *inspiration* from Headroom's techniques (they got a lot right), but implement as a native component of compact with its own license, tests, and audit trail. Adopt patterns, not code.

---

## §3 · Telemetry gap analysis

An organization deploying Claude Code broadly typically has three telemetry surfaces already available:

| Surface | What it gives | What it can't give |
|---|---|---|
| **Anthropic Analytics** (org dashboard) | Total tokens per API key · Model breakdown · Per-day trends · Cache-read/write/input split · Estimated $ cost | Per-user attribution (all devs share the org key — Anthropic sees one blob) · Per-workflow / per-command · Per-repo · Per-team |
| **MCP server-side usage tracking** | Per-tool call rate on server-owned MCPs, response times, error rates | Nothing about non-MCP work · No token counts for the calling Claude · Only your own MCPs |
| **Claude Code hooks** (Stop, PreToolUse, PostToolUse, etc.) | Session lifecycle events · Tool invocations · Per-command emitted events | Token counts per call (not passed into hooks by Claude Code today) · Which model was used · Tool result *size* |

### The actual gap

Existing surfaces answer "**what happened**". They don't answer "**how many tokens did that cost, attributed to whom, doing what**".

Specifically missing:
1. Per-call token accounting (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `model`)
2. Attribution beyond MCP — most spend is on `Read` / `Grep` / `Edit` / free-form chat, which never touches any tracker
3. Per-user / per-team roll-up
4. Session-level breakdown (was this session's cost 1 big call or 200 chatty turns?)

### Three paths to close the gap

| Path | How | Fidelity | Effort |
|---|---|---|---|
| **A · Local approximation via tiktoken** | Hook counts characters + tokenizes locally before/after each tool call | ~95% accurate, no upstream integration | ~day |
| **B · Anthropic Analytics API reconciliation** | Query org Analytics for per-day roll-ups, join to event stream by user + timestamp | Ground-truth totals, weak per-call attribution | Days, API-dependent |
| **C · Gateway captures API response `usage.*`** | Proxy sees full response body, extracts usage fields into event stream | Perfect — matches Anthropic's own accounting | Weeks (gateway build) |

**Recommended**: Path A first (fast, unblocks measurement), Path C when the gateway is ready.

---

## §4 · The 47-item optimization framework

Nothing dropped. Renumbered from scratch. Every item tagged with:
- **Where**: Plugin / Config / MCP / Gateway / Behavior
- **Effort**: Hours / Day / Week / Weeks / Quarter
- **Impact**: S / M / L / XL on org-wide token spend
- **OSS?**: Generic (portable) or Org-specific (custom-per-org via extension)

### A · Fixed-prompt shrink (paid on every call, prefix-cached)

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 1 | Skill diet — demote always-on skills to model-invoked where "always" isn't load-bearing | Plugin | Day | M | Generic |
| 2 | Trim SKILL.md bodies; move deep refs to companion files loaded on demand | Plugin | Day | S | Generic |
| 3 | Progressive skill disclosure — thin SKILL.md + on-demand `prompts/*.md` per phase | Plugin | Day | M | Generic |
| 4 | Per-subagent tool allowlisting — trim `tools:` frontmatter to actual need | Plugin | Hours | S | Generic |
| 5 | MCP tool deferred loading — server marks tools deferred so schemas load on demand via ToolSearch | MCP | Days/server | **L** | Generic |
| 6 | Trim CLAUDE.md — terse project instructions, no marketing prose | Config | Hours | S | Generic |
| 7 | Structured tool schemas — dense JSON over verbose prose in tool descriptions | MCP | Days/server | S | Generic |

### B · Variable-prompt shrink (tool responses — biggest cost surface)

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 8 | Bake into always-on skill: `Read` offset+limit; narrow `Grep`; never dump full dirs | Plugin (skill) + Config (CLAUDE.md) | Day | **L** | Generic |
| 9 | MCP server-side pagination + response caps | MCP | Week/server | **L** | Generic |
| 10 | Bash-output capping — pipe long outputs through `head`; hook truncates results > N lines | Plugin (hooks + skill) | Day | M | Generic |
| 11 | JSON tool-response compression (SmartCrusher-inspired pattern, native impl) | Gateway | Weeks | **XL** | Generic |
| 12 | Reversible compression + retrieve tool (CCR-inspired pattern, native impl) | Gateway | Weeks | **XL** | Generic |
| 13 | Context deduplication — don't inject same file/MCP result twice in a session | Gateway | Weeks | M | Generic |
| 14 | Response truncation heuristics — head/tail slice, drop middle if not code | Plugin (hooks) or Gateway | Day / Weeks | M | Generic |
| 15 | Batch MCP queries — bundle N related lookups (`get_jira_issues([...])`, `batch_search`) | MCP + Plugin | Day per surface | M | Generic |
| 16 | Client-side MCP response cache (session-scoped) | Plugin (hooks) or Gateway | Week | M | Generic |

### C · Prompt caching (Anthropic 5-min TTL — free money on stable prefix)

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 17 | Prefix stability — put stable content at prompt top | Plugin (guide) + Gateway (enforce) | Hours + Weeks | S | Generic |
| 18 | Deterministic ordering — sort tools/skills/JSON keys byte-identically | Gateway | Weeks | M | Generic |
| 19 | Explicit `cache_control` markers via SDK | Gateway | Weeks | M | Generic |
| 20 | `prompt_cache_key` pooling — key = team; N devs share cache within 5-min window | Gateway | Week | **XL** | Generic |
| 21 | Session-cross-restart persistence — serialize conversation so cache survives Cmd+Q | Plugin (hooks) + Gateway | Week | S | Generic |

### D · Smart routing (model tier, skip-Claude-entirely)

**Note on auto-escalating routers**: naive LLM-triage routing adds a Haiku call before every request, roughly doubling cost. **Do not implement this way.** Use **static routing** based on primitive type or intent signal (slash command frontmatter, subagent frontmatter, message-shape heuristics, explicit user override). Zero triage overhead. Item 22 below is the correct pattern; item 24 uses declarative rules (no LLM in the routing decision).

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 22 | Model tier per subagent / command — static, config-based | Plugin | Day | M | Generic |
| 23 | Account-level default → Haiku for chat, Sonnet for code, Opus opt-in | Config | Hours | M | Generic |
| 24 | Rules-based intent routing (declarative heuristics, no extra LLM call) | Gateway | Week | M | Generic |
| 25 | Deterministic scripts replace trivial Claude calls (lint, format, secret-scan, complexity) | Plugin (scripts + hooks) | Day per gate | **L** | Generic |
| 26 | Self-hosted / fine-tuned model for high-volume repetitive tasks | Infra project | Quarter+ | **XL** | Extension |
| 27 | Local Ollama-tier model for classification / summarization | Gateway | Weeks | M | Generic |
| 28 | Semantic Q→A cache — identical/near-identical questions return cached answers | Gateway | Weeks | M | Generic |

### E · Output-token discipline (output ~5× input price)

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 29 | Enforce brevity in subagent + skill prompts ("under N words", "no summary") | Plugin | Day | M | Generic |
| 30 | Structured outputs (JSON schema) for subagent returns — no prose overhead | Plugin | Day per agent | S | Generic |
| 31 | Stream-and-truncate — cancel completion early on drift signals | Gateway | Week | S | Generic |

### F · Observability + budgets (measure first)

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 32 | Token telemetry hook (Path A — local tiktoken approx on Stop/PreToolUse) | Plugin | Day | **prereq** | Generic |
| 33 | Token telemetry via gateway (Path C — real `usage.*` from API response) | Gateway | Weeks | **prereq (higher fidelity)** | Generic |
| 34 | Cost analytics MCP — reads telemetry stream, returns per-user/team/workflow rollups | Plugin (MCP) + backend | Days | M | Generic (backend via extension) |
| 35 | `/compact:cost` slash command — leadership dashboard | Plugin | Day | M | Generic |
| 36 | Per-user / per-team hard budgets | Plugin (hooks) + Gateway | Day + Week | M | Generic |
| 37 | Slack/webhook alerts when session exceeds threshold | Plugin (hooks) | Day | S | Generic |

### G · Org-wide defaults (affects every dev, every session)

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 38 | Canonical CLAUDE.md template — devs drop into their repos | Config template | Hours | **L** | Generic |
| 39 | Canonical `.claude/settings.json` — permission/tool allowlist, telemetry sink, model default | Config template | Hours | **L** | Generic |
| 40 | Endorsed MCP set — "these are the MCPs to keep; disable rest by default" | Config template + docs | Hours | M | Extension |
| 41 | Frugal-prompting always-on skill — teaches Claude to be frugal regardless of dev's ask | Plugin | Day | **L** | Generic |

### H · Behavioral / cultural

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 42 | Session hygiene training + docs — `/clear` on topic switch, don't paste 5000-line logs | Behavior + Docs | Ongoing | M | Generic |
| 43 | Batch questions instead of chatty back-and-forth | Behavior | Ongoing | S | Generic |
| 44 | Reminder hook on Stop if session used > threshold | Plugin (hooks) | Day | S | Generic |

### I · Session lifecycle

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 45 | Idle-session cleanup — auto-close after N minutes idle | Plugin (hooks) | Day | S | Generic |
| 46 | Conversation compaction tuning — thresholds + preserve strategy | Plugin (config) or Gateway | Day | M | Generic |

### J · Cross-session memory

| # | Item | Where | Effort | Impact | OSS |
|---|---|---|---|---|---|
| 47 | Anthropic Memory API — persistent facts survive across sessions without re-prompting | Plugin + Config | Days | M | Generic |

---

## §5 · Product architecture

### Working name

**compact** (final name TBD — could also be `terse`, `curb`, `sip`, `frugal`, etc. — pick whatever markets best. Used throughout as placeholder.)

### Five layers

```
                    ┌─────────────────────────────────┐
                    │  compact-docs (mkdocs site)     │
                    │  + benchmarks + comparison      │
                    └─────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
  ┌───────────┐             ┌───────────────┐           ┌─────────────┐
  │  plugin   │             │   templates   │           │ mcp-toolkit │
  │           │             │               │           │             │
  │ - skills  │             │ - CLAUDE.md   │           │ - patterns  │
  │ - cmds    │             │ - settings    │           │ - helpers   │
  │ - hooks   │             │   .json       │           │ - examples  │
  │ - MCP     │             │ - hygiene doc │           │             │
  │   (local  │             │ - extension   │           │  (for MCP   │
  │   sink)   │             │   marker      │           │  authors)   │
  └───────────┘             └───────────────┘           └─────────────┘
                                    │
                                    ▼ (optional, deploys separately)
                          ┌───────────────────┐
                          │      gate         │
                          │  transport proxy  │
                          │                   │
                          │  compression      │
                          │  cache pooling    │
                          │  routing          │
                          │  budgets          │
                          │  usage capture    │
                          │  extension points │
                          └───────────────────┘
                                    │
                                    ▼
                          ┌───────────────────┐
                          │  contrib/         │
                          │                   │
                          │  community        │
                          │  - auth adapters  │
                          │  - routing rules  │
                          │  - skill overlays │
                          │  - backend impls  │
                          └───────────────────┘
```

### Design principles

1. **Any single layer is independently useful.** Install the plugin alone → 10-25% savings. Add templates → 15-30%. Add gate → 40-60% on tool-heavy workflows.
2. **Extensions never fork the core.** Four extension points (§7) let organizations plug their own MCPs, skills, backends, and routing rules without modifying compact code.
3. **Static routing, not LLM triage.** Every routing decision is declarative (frontmatter, config file, heuristic rules). Zero triage overhead.
4. **Measure before optimize.** F32/F33 (telemetry) are prerequisites for tuning. Ship the measurement path first.
5. **Prefix stability is free money.** Anthropic's 5-min cache TTL means byte-identical prefixes get ~90% discount. Any strategy that busts the cache is worse than a slightly larger stable prefix.

### Repo layout

```
compact/
├── LICENSE                       Apache 2.0
├── README.md
├── ROADMAP.md
├── BENCHMARKS.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
│
├── compact-plugin/               Claude Code plugin — install via /plugin marketplace add
│   ├── .claude-plugin/plugin.json
│   ├── skills/
│   │   ├── compact-frugal/       always-on frugal-prompting skill
│   │   │   └── SKILL.md
│   │   └── compact-hygiene/      model-invoked session hygiene guidance
│   │       └── SKILL.md
│   ├── commands/
│   │   ├── compact-cost.md       leadership cost dashboard
│   │   ├── compact-audit.md      audit a session's spend line by line
│   │   ├── compact-budget.md     set / view / enforce budgets
│   │   ├── compact-doctor.md     scans env for waste (unused MCPs, oversized skills)
│   │   └── compact-warmup.md     pre-auth for any Okta/OAuth-gated MCPs
│   ├── hooks/
│   │   └── hooks.json            SessionStart, PreToolUse, PostToolUse, Stop
│   ├── scripts/
│   │   ├── token_count.py        tiktoken-based approximation (item 32)
│   │   ├── truncate_output.py    caps tool results at N lines/bytes (10, 14)
│   │   ├── budget_enforce.py     per-user/team hard budgets (36)
│   │   ├── budget_alert.py       Slack/webhook alerts on threshold (37)
│   │   ├── idle_cleanup.py       auto-close idle sessions (45)
│   │   ├── mcp_hygiene_scan.py   detects unused MCPs (40)
│   │   ├── mcp_response_cache.py session-local MCP response cache (16)
│   │   ├── session_summary.py    emits token summary on Stop (44)
│   │   └── skills_audit.py       reports on always-on skill diet (1, 2, 4)
│   └── mcpServers/
│       └── compact-cost/         local file-sink MCP for cost analytics (34)
│           ├── server.py         stdio MCP
│           └── backend.py        Protocol interface for pluggable backends
│
├── compact-templates/            drop-in configs any repo/org uses
│   ├── CLAUDE.md.template        canonical frugal instructions + extension marker (38)
│   ├── settings.json.template    permission/tool allowlist, telemetry sink,
│   │                             model default, compaction threshold (39, 23, 46)
│   ├── mcp-hygiene.md            which MCPs to keep, disable-by-default guidance (40)
│   └── example-extension/        how to layer org-specific overlays
│       ├── CLAUDE.md.example
│       └── README.md
│
├── compact-mcp-toolkit/          libraries for MCP authors
│   ├── py/
│   │   ├── compact_mcp/
│   │   │   ├── deferred.py       decorators for deferred tool registration (5)
│   │   │   ├── pagination.py     paginated response helpers (9)
│   │   │   ├── truncate.py       structured response truncation
│   │   │   ├── batch.py          batch-query builder (15)
│   │   │   └── schema.py         dense structured-schema helpers (7)
│   │   ├── examples/
│   │   └── tests/
│   ├── ts/
│   │   └── (mirror of py/ for TypeScript MCP authors)
│   └── docs/
│       ├── deferred-loading.md   pattern guide
│       ├── structured-schemas.md pattern guide
│       ├── pagination.md         pattern guide
│       └── batch-queries.md      pattern guide
│
├── compact-gate/                 OPTIONAL transport proxy (Rust)
│   ├── src/
│   │   ├── main.rs
│   │   ├── proxy/                the wire layer
│   │   ├── compression/
│   │   │   ├── smart_crusher.rs  JSON stat compression (11)
│   │   │   ├── ccr.rs            compress-cache-retrieve (12)
│   │   │   └── truncate.rs       response truncation heuristics (14)
│   │   ├── caching/
│   │   │   ├── prefix_stable.rs  put stable prefix at top (17)
│   │   │   ├── deterministic.rs  canonical ordering (18)
│   │   │   ├── cache_control.rs  explicit breakpoints (19)
│   │   │   ├── pool.rs           prompt_cache_key by team/user (20)
│   │   │   └── session.rs        cross-restart persistence (21)
│   │   ├── context/
│   │   │   ├── dedup.rs          drop repeated file/tool content (13)
│   │   │   └── semantic_cache.rs Q→A cache (28)
│   │   ├── routing/
│   │   │   ├── rules.rs          declarative rules engine (24)
│   │   │   ├── local_model.rs    Ollama offload for classification (27)
│   │   │   └── stream_abort.rs   cancel drifting completions (31)
│   │   ├── telemetry/
│   │   │   └── usage.rs          capture usage.* from API response body (33)
│   │   ├── budgets/
│   │   │   └── enforce.rs        server-side hard budgets (36)
│   │   └── extensions/           dyn-loaded adapters
│   │       ├── auth_adapter.rs   trait
│   │       ├── routing_rules.rs  trait
│   │       └── backend.rs        trait
│   ├── config/
│   │   └── compact-gate.yaml.example
│   ├── docker/
│   │   ├── Dockerfile
│   │   └── docker-compose.yml
│   ├── benchmarks/
│   └── docs/
│       ├── quickstart.md
│       ├── deployment.md
│       ├── extending.md
│       ├── compression.md
│       ├── cache-pooling.md
│       ├── routing.md
│       └── budgets.md
│
├── compact-docs/                 mkdocs top-level site
│   ├── mkdocs.yml
│   ├── quickstart.md
│   ├── how-it-works.md           the 47-item framework, top-to-bottom
│   ├── extending.md              plug your own org config in
│   ├── benchmarks.md
│   ├── comparison.md             vs Headroom / LiteLLM / others
│   └── faq.md
│
├── benchmarks/                   reproducible cost + accuracy benchmarks
│   ├── datasets/                 fixture workloads
│   ├── plugin-only/              plugin installed, no gate
│   ├── plugin-plus-gate/         full stack
│   ├── vs-baseline/              raw Claude Code
│   ├── vs-headroom/              side-by-side
│   ├── run.sh                    reproducible harness
│   └── results/
│
└── contrib/                      community-contributed
    ├── auth-adapters/
    ├── routing-rules/
    ├── skill-extensions/
    ├── backend-impls/
    └── README.md
```

---

## §6 · All 47 items — mapped to code locations

| # | Item | Home in compact | Effort | Impact |
|---|---|---|---|---|
| 1 | Skill diet | `compact-plugin/scripts/skills_audit.py` + `compact-docs/how-it-works.md#skill-diet` | Day | M |
| 2 | Trim SKILL.md bodies | `compact-docs/how-it-works.md#trim-skills` + `skills_audit.py` warns on oversized | Day | S |
| 3 | Progressive skill disclosure | `compact-docs/how-it-works.md#progressive-disclosure`; `compact-plugin/skills/compact-frugal/` demonstrates | Day | M |
| 4 | Per-subagent tool allowlist | `compact-docs/how-it-works.md#tool-allowlist`; `skills_audit.py` warns on unused | Hours | S |
| 5 | MCP deferred loading | `compact-mcp-toolkit/py/compact_mcp/deferred.py` + `docs/deferred-loading.md` | Days per MCP | **L** |
| 6 | Trim CLAUDE.md | `compact-templates/CLAUDE.md.template` (lean baseline) | Hours | S |
| 7 | Structured tool schemas | `compact-mcp-toolkit/py/compact_mcp/schema.py` + `docs/structured-schemas.md` | Days per MCP | S |
| 8 | Frugal Read/Grep/Bash discipline in always-on skill | `compact-plugin/skills/compact-frugal/SKILL.md` + `compact-templates/CLAUDE.md.template` | Day | **L** |
| 9 | MCP pagination + response caps | `compact-mcp-toolkit/py/compact_mcp/pagination.py` + `docs/pagination.md` | Week per MCP | **L** |
| 10 | Bash-output capping via hook | `compact-plugin/scripts/truncate_output.py` (Pre/PostToolUse) | Day | M |
| 11 | JSON tool-response compression | `compact-gate/src/compression/smart_crusher.rs` | Weeks | **XL** |
| 12 | Reversible compress + retrieve | `compact-gate/src/compression/ccr.rs` + SQLite; injects `compact_retrieve` tool | Weeks | **XL** |
| 13 | Context deduplication | `compact-gate/src/context/dedup.rs` | Weeks | M |
| 14 | Response truncation heuristics | `compact-gate/src/compression/truncate.rs` (plus plugin fallback in `truncate_output.py`) | Day / Weeks | M |
| 15 | Batch MCP queries | `compact-mcp-toolkit/py/compact_mcp/batch.py` + `docs/batch-queries.md` | Day per pattern | M |
| 16 | Client-side MCP response cache | `compact-plugin/scripts/mcp_response_cache.py` OR `compact-gate/src/context/mcp_cache.rs` | Week | M |
| 17 | Prefix stability | `compact-gate/src/caching/prefix_stable.rs`; guide in `compact-docs/how-it-works.md` | Hours (guide) / Weeks (proxy) | S |
| 18 | Deterministic ordering | `compact-gate/src/caching/deterministic.rs` | Weeks | M |
| 19 | Explicit cache_control markers | `compact-gate/src/caching/cache_control.rs` | Weeks | M |
| 20 | prompt_cache_key pooling | `compact-gate/src/caching/pool.rs` | Week | **XL** |
| 21 | Session persistence | `compact-plugin/hooks/hooks.json` + `compact-gate/src/caching/session.rs` | Week | S |
| 22 | Static model tier per subagent / command | `compact-templates/settings.json.template` + `compact-docs/how-it-works.md#model-tier`; per-agent frontmatter | Day | M |
| 23 | Account-level model default | `compact-templates/settings.json.template` | Hours | M |
| 24 | Rules-based intent routing | `compact-gate/src/routing/rules.rs` + `config/compact-gate.yaml.example` | Week | M |
| 25 | Deterministic scripts replace Claude | `compact-plugin/scripts/` for common patterns; `compact-docs/how-it-works.md#deterministic-replacement` | Day per pattern | **L** |
| 26 | Self-hosted / fine-tuned backing | `compact-gate/src/extensions/backend.rs` trait; `contrib/backend-impls/` | Quarter+ | **XL** |
| 27 | Local Ollama-tier classification | `compact-gate/src/routing/local_model.rs` | Weeks | M |
| 28 | Semantic Q→A cache | `compact-gate/src/context/semantic_cache.rs` | Weeks | M |
| 29 | Brevity in subagent/skill prompts | `compact-plugin/skills/compact-frugal/SKILL.md` (org-wide default) | Day | M |
| 30 | Structured outputs for subagents | `compact-docs/how-it-works.md#structured-output`; per-agent frontmatter | Day per agent | S |
| 31 | Stream-and-truncate | `compact-gate/src/routing/stream_abort.rs` | Week | S |
| 32 | Token telemetry hook (Path A) | `compact-plugin/scripts/token_count.py` (PreToolUse + Stop) | Day | prereq |
| 33 | Token telemetry via gateway (Path C) | `compact-gate/src/telemetry/usage.rs` | Weeks | prereq |
| 34 | Cost analytics MCP | `compact-plugin/mcpServers/compact-cost/` (`backend.py` Protocol); `contrib/backend-impls/` | Days | M |
| 35 | `/compact:cost` command | `compact-plugin/commands/compact-cost.md` | Day | M |
| 36 | Hard budgets | `compact-plugin/scripts/budget_enforce.py` + `compact-gate/src/budgets/enforce.rs` | Day + Week | M |
| 37 | Slack/webhook alerts | `compact-plugin/scripts/budget_alert.py` | Day | S |
| 38 | Canonical CLAUDE.md template | `compact-templates/CLAUDE.md.template` with extension marker | Hours | **L** |
| 39 | Canonical settings.json template | `compact-templates/settings.json.template` | Hours | **L** |
| 40 | Endorsed MCP set + hygiene | `compact-templates/mcp-hygiene.md`; scanner `compact-plugin/scripts/mcp_hygiene_scan.py` | Hours | M |
| 41 | Frugal-prompting always-on skill | `compact-plugin/skills/compact-frugal/SKILL.md` | Day | **L** |
| 42 | Session hygiene docs | `compact-plugin/skills/compact-hygiene/SKILL.md` + `compact-docs/how-it-works.md#session-hygiene` | Ongoing | M |
| 43 | Batch questions guidance | `compact-plugin/skills/compact-hygiene/SKILL.md` | Ongoing | S |
| 44 | Stop-hook token reminder | `compact-plugin/scripts/session_summary.py` | Day | S |
| 45 | Idle-session cleanup | `compact-plugin/scripts/idle_cleanup.py` | Day | S |
| 46 | Conversation compaction tuning | `compact-templates/settings.json.template` + `compact-gate` can enforce | Day | M |
| 47 | Anthropic Memory API | `compact-plugin/skills/compact-frugal/SKILL.md` teaches; `compact-gate/src/context/` can inject | Days | M |

**All 47 items have a home. No item is dropped.**

---

## §7 · Extension mechanism (four extension points)

Orgs plug their own stuff in without forking the core:

### Extension 1: Overlay skills (ship a private companion plugin)

An org publishes a private plugin alongside `compact` that adds always-on skills specific to that org. Example: an org that wants to teach Claude their internal architecture conventions. Load order: `compact-frugal` (from compact) + `myorg-conventions` (from private extension) both auto-load. Zero merge conflict — they're separate skills in separate plugins.

### Extension 2: CLAUDE.md extension marker

The canonical `CLAUDE.md.template` includes an extension marker:

```markdown
# compact frugality defaults
- prefer Read offset+limit ...
- cap Bash output at 200 lines ...
- structured output over prose ...
...

<!-- COMPACT_EXTENSION_MARKER -->
# Org-specific instructions below
# (Your org adds internal service pointers, conventions, etc. here)
```

Orgs edit only the section below the marker. A `compact update` template refresher CLI won't clobber the tail.

### Extension 3: Cost backend adapter

The `compact-cost` MCP has a Python Protocol for the storage backend:

```python
# compact_plugin/mcpServers/compact-cost/backend.py
class CostBackend(Protocol):
    def ingest(self, event: TokenEvent) -> None: ...
    def query(self, filters: QueryFilter) -> Rollup: ...
    def export(self, format: str) -> bytes: ...

# Default: local SQLite at ~/.compact/cost.db
class LocalSQLiteBackend: ...
```

Orgs implement the Protocol in a separate module + point config at it:

```yaml
# ~/.compact/config.yaml
cost_backend:
  module: myorg_compact.warehouse_backend
  class: WarehouseBackend
  config:
    warehouse_url: ...
```

### Extension 4: Gate config file

`compact-gate` is fully driven by a YAML config with pluggable rule files:

```yaml
# compact-gate.yaml
routing:
  rules_file: ./myorg-routing.yaml
auth:
  adapter: myorg_auth.SsoAdapter
budgets:
  policy_file: ./myorg-budgets.yaml
compression:
  strategies: [smart-crusher, ccr]
  extensions: []
telemetry:
  export_to: myorg_warehouse
```

All extensions live outside the compact repo. compact never needs to know about org internals.

---

## §8 · Phasing plan

### Phase 1 — Repo scaffolding + `compact-plugin` (day 1)

- Create OSS repo with the full directory structure (empty stubs where needed)
- Implement `compact-plugin/`:
  - Frugal skill (§4 item 41)
  - Hygiene skill (42, 43)
  - 5 slash commands (35 cost, audit, budget, doctor, warmup)
  - 8 hook scripts (token_count 32, truncate_output 10/14, budget_enforce 36, budget_alert 37/44, idle_cleanup 45, mcp_hygiene_scan 40, mcp_response_cache 16, session_summary 44, skills_audit 1/2/4)
  - Local cost MCP with SQLite backend (34)

**Items shipped in Phase 1**: 1, 2, 3, 4, 6, 8, 10, 14 (plugin variant), 15 (docs), 22, 23, 25, 29, 30, 32, 34, 35, 36 (plugin variant), 37, 38, 39, 40, 41, 42, 43, 44, 45, 46 (plugin variant).

**That's ~30 of 47 items in one day.**

### Phase 2 — `compact-templates` + `compact-mcp-toolkit` (day 2)

- Author canonical CLAUDE.md.template with extension marker (38)
- Author settings.json.template with all defaults (39, 23, 46)
- Write mcp-hygiene.md guidance (40)
- Example-extension worked example
- Implement `compact-mcp-toolkit` in Python (TS mirror later): deferred (5), pagination (9), truncate (14), batch (15), schema (7) helpers + docs

**Items shipped in Phase 2**: 5, 7, 9, 15 (helpers as reusable library).

### Phase 3 — `compact-docs` + `benchmarks` skeleton + v0.1 release (day 3)

- mkdocs site with all 47 items documented on `how-it-works.md`
- Benchmark harness that runs a fixture workload with/without plugin and reports tokens/cost delta
- `comparison.md` — honest side-by-side with Headroom / LiteLLM / others
- Publish v0.1 to public GitHub
- Write launch post (technical, not marketing)

**Items shipped in Phase 3**: docs anchor for all 47 items. Ready to accept community feedback.

### Phase 4 — `compact-gate` MVP (days 4-6)

- Rust project scaffold; proxy terminates HTTPS and forwards to `api.anthropic.com`
- Implement in order (highest ROI first):
  1. **11** — SmartCrusher-inspired JSON compression (biggest single win)
  2. **20** — `prompt_cache_key` pooling (second biggest)
  3. **17-18** — prefix stability + deterministic ordering
  4. **13** — context dedup
  5. **33** — real usage capture from API response body
  6. **36** — server-side budget enforcement

**Items shipped in Phase 4**: 11, 13, 17-20, 24 (rules engine skeleton), 33, 36 (server variant).

### Phase 5 — Advanced gate features (days 7+)

- **12** CCR (reversible compress-cache-retrieve with SQLite; inject retrieve tool)
- **14 (server)** — full response truncation heuristics
- **21** session persistence
- **26-28** routing extensions (Ollama offload, semantic cache, custom backend traits)
- **47** Anthropic Memory API integration

### Timeline realism

| Completion | State |
|---|---|
| End of day 1 | Full plugin + local cost MCP + all hook scripts working end-to-end |
| End of day 2 | Templates + MCP toolkit + docs skeleton |
| End of day 3 | Docs mature + benchmarks running + published as v0.1 OSS |
| End of week 1 | Gate MVP with compression + cache pooling — v0.5 |
| End of week 2 | Advanced gate features — v0.9 |
| End of week 3 | Polish + governance + first external contributor — v1.0 |

Day 1 is realistic with AI-assisted coding. Gate is Rust — AI-assist helps but not instant.

---

## §9 · Comparison vs Headroom (for `compact-docs/comparison.md`)

| Dimension | compact | Headroom |
|---|---|---|
| Scope | Full-stack framework (5 layers) | Transport proxy only |
| Distribution | Plugin + templates + toolkit + optional gate | Proxy or wrap-CLI |
| Cost telemetry with per-user/team/workflow attribution | Yes (plugin + gate both emit) | Partial (proxy-level only) |
| Frugal-prompting skill for the model | Yes (always-on) | No |
| CLAUDE.md + settings.json templates | Yes | No |
| MCP author toolkit (deferred, pagination, batching patterns) | Yes | No |
| Prompt-cache pooling across users | Yes (via `prompt_cache_key`) | Partial (their CacheAligner) |
| JSON compression | Yes (SmartCrusher-inspired, native impl) | Yes (their SmartCrusher) |
| Reversible compression + retrieve tool | Yes (CCR-inspired, native impl) | Yes (their CCR) |
| Semantic Q→A cache | Yes (v0.9+) | No |
| Local Ollama offload for classification | Yes | No |
| Governance | OSS, Apache 2.0, multi-contributor | Single-vendor |
| Language | Python plugin + Rust gate | Rust |
| Deployment | Optional gate — plugin alone works | Requires proxy |

**Pitch**: install compact if you want measurable Claude Code cost reduction without needing to run a proxy AND with the option to add one when you're ready. Install Headroom if you only want transport-layer benefits and are willing to run a proxy.

---

## §10 · Open decisions

The following need answers before Phase 1 starts:

1. **Product name** — `compact` (working name; final TBD). Alternatives: `terse`, `curb`, `sip`, `frugal`, `prune`, `trim`. Vote for `compact` (verb + noun, matches theme).
2. **License** — Apache 2.0 (safe, corp-friendly). Alternative: MIT. Or dual (Apache 2.0 core + GPL gate). Vote for **Apache 2.0**.
3. **Repo location** — where the OSS repo lives. Personal GitHub? New GitHub org? Some other host?
4. **Gateway language** — Rust (matches Headroom, ecosystem strong) vs Go (easier to distribute, slower). Vote for **Rust**.
5. **Should the gate embed Ollama or shell out to a local install** — embedded is easier for users but larger binary; shell-out requires the user to install Ollama. Vote for **shell-out** (leaner distribution).

---

## §11 · Getting started (what to build first)

If you're a fresh Claude Code session reading this to start work, here's the recommended order:

### Step 1 — Set up the repo (30 min)

```bash
# From wherever you keep projects:
gh repo create <owner>/compact --public --license apache-2.0
cd compact
mkdir -p compact-plugin/{.claude-plugin,skills,commands,hooks,scripts,mcpServers} \
         compact-templates \
         compact-mcp-toolkit/py/compact_mcp/{tests,examples} compact-mcp-toolkit/ts \
         compact-gate/src compact-gate/config compact-gate/docker compact-gate/benchmarks compact-gate/docs \
         compact-docs benchmarks contrib
```

Add `README.md`, `LICENSE` (Apache 2.0), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `ROADMAP.md` stubs.

### Step 2 — Implement Phase 1 (rest of day 1)

Build the plugin end-to-end (§8 Phase 1 list). Ship:

- `compact-plugin/.claude-plugin/plugin.json` (v0.1.0, plugin name `compact`, describes what's included)
- `compact-plugin/skills/compact-frugal/SKILL.md` — always-on, teaches frugal Read/Grep/Bash discipline, brevity, structured output, session hygiene reminders
- `compact-plugin/skills/compact-hygiene/SKILL.md` — model-invoked on long-session detection
- 5 command markdowns (`compact-cost.md`, `compact-audit.md`, `compact-budget.md`, `compact-doctor.md`, `compact-warmup.md`)
- `hooks/hooks.json` wiring the 8 hook scripts
- `scripts/token_count.py` — `tiktoken` approximation, emits JSON events to `~/.compact/telemetry/*.jsonl`
- Other 7 scripts per the layout
- `mcpServers/compact-cost/` — stdio MCP that reads the telemetry sink, returns per-user/team/workflow rollups, backed by SQLite

Each script should be small (100-300 lines), well-commented, and independently testable.

**Smoke-test on your own machine**:
- Install the plugin via `/plugin install <local-path>`
- Run a real Claude Code session for 30 minutes
- Verify `~/.compact/telemetry/` has events with token counts
- Run `/compact:cost` — should show your session's spend

### Step 3 — Templates + toolkit (day 2)

Fill Phase 2's contents (§8). Author the canonical CLAUDE.md and settings.json with sensible defaults. Build the MCP toolkit library in Python (docs first, then code — makes for cleaner API).

### Step 4 — Docs + benchmarks + v0.1 release (day 3)

`compact-docs/how-it-works.md` should systematically cover all 47 items — each with a heading, the problem, the solution as compact implements it, and a code pointer. Roughly one paragraph per item.

Benchmarks: pick 3-5 fixture workloads (e.g., "refactor a 500-file repo", "PR review", "debug a stack trace") and run them with/without compact. Report the delta as $ and %.

Publish. Announce.

### Step 5 — Gate MVP (week 1)

Rust project. Start with the proxy skeleton (`hyper` + `tokio` + `axum` — standard stack). Add compression first (biggest ROI), then cache pooling, then dedup, then usage capture. Each feature is a middleware layer.

### Step 6 — Advanced features + v1.0 (weeks 2-3)

CCR, session persistence, semantic cache, local model offload, Memory API. Polish docs. Recruit an external contributor. Cut v1.0.

---

## §12 · Notes for a fresh Claude session

If you're picking this up cold, here are things you should know that aren't obvious from the spec:

- **The 47 items are exhaustive.** Every plausible token-optimization strategy for Claude Code is in the framework. If you think of another, add it and update this doc — but audit first, most "new" ideas fit under an existing item.
- **Static routing, not LLM triage.** If someone suggests "have Claude classify then route", push back — it doubles cost naively. See §4 D note.
- **Prefix stability is free money.** Any change that busts the Anthropic 5-min cache TTL is worse than a slightly larger stable prefix. When in doubt, keep the prefix stable.
- **Measure before optimize.** F32/F33 (telemetry) are prerequisites. Ship them first.
- **The extension mechanism is the OSS moat.** Every org has custom needs (auth, backends, routing rules, org-specific skills). Extensions let them customize without forking. Never let a feature ship that requires forking to customize — put an extension point in instead.
- **Don't bundle Headroom.** We implement patterns natively for governance and coupling reasons. See §2.
- **Apache 2.0 unless overruled.** Corp-friendly, permissive.
- **Repo layout is prescriptive.** The tree in §5 isn't a suggestion — it's the design. Deviate only for good reason and update this doc.

---

## §13 · Contact

This design was authored by a Claude Code session on `2026-07-09` (Pacific). The original session context isn't preserved here — everything material to continuing the work is in this document.

If a fresh session hits a decision point not covered here, prefer:
- Bias to shipping the smallest useful thing quickly
- Bias to Apache 2.0 semantics
- Bias to static/declarative configuration over runtime LLM decisions
- Bias to plugin-first (fastest to adopt) over gateway-first (highest ceiling but slower to build)

---

**End of design brief.**
