# TokenDog — Design Spec

**Status:** Approved for implementation (2026-07-12)
**Master design:** `compact-DESIGN.md` (the full 47-item framework; this spec sits on top of it and governs where the two disagree)

---

## 1 · What TokenDog is

TokenDog is an open-source, full-stack framework for reducing Claude Code token spend across a developer organization (target: 30–60% reduction, no quality loss). It is **plugin-first** — the plugin alone delivers value; the transport proxy ("gate") is optional and deploys separately.

The name is a **watchdog** metaphor: it watches token spend and barks when budgets are exceeded. This is honest — observability + budgets + alerts are a core pillar, not decoration. The core is **deterministic** (static routing, statistical compression, cache pooling, budgets); there are no ML/"neural" claims.

## 2 · Locked decisions (resolves brief §10)

| # | Decision | Choice | Note |
|---|---|---|---|
| 1 | Product name | **TokenDog** | Replaces placeholder `compact` everywhere. CLI handle `tokendog`; commands `/tokendog:*`. |
| 2 | License | **Apache 2.0** | Corp-friendly, permissive. |
| 3 | Repo location | **TBD** | Does not block local build; user will decide + verify name availability (GitHub/npm/domain). |
| 4 | Gateway language | **Rust** | Matches ecosystem; brief default. |
| 5 | Ollama | **Shell-out** | Leaner distribution; no embedded model. |
| 6 | Neural/semantic layer | **Deferred, not core** | Embedding-based semantic cache is at most a later *feature*, never the brand. See brief §Path-1 analysis. |

## 3 · Naming migration

Every `compact*` identifier in the brief maps to `tokendog*`:

| Brief (placeholder) | TokenDog |
|---|---|
| `compact-plugin` | `tokendog-plugin` |
| `compact-templates` | `tokendog-templates` |
| `compact-mcp-toolkit` | `tokendog-mcp-toolkit` |
| `compact-gate` | `tokendog-gate` (internal layer name stays "gate") |
| `compact-docs` | `tokendog-docs` |
| `/compact:cost` etc. | `/tokendog:cost` etc. |
| `~/.compact/` | `~/.tokendog/` |
| Python pkg `compact_mcp` | `tokendog_mcp` |
| Env/config `COMPACT_EXTENSION_MARKER` | `TOKENDOG_EXTENSION_MARKER` |

## 4 · Architecture (summary — full detail in `compact-DESIGN.md` §5)

Five layers, each independently useful:

1. **tokendog-plugin** — Claude Code plugin: always-on frugal skill, hygiene skill, 5 slash commands, hook scripts, local cost-analytics MCP (SQLite).
2. **tokendog-templates** — drop-in `CLAUDE.md` + `settings.json` baselines, MCP hygiene guidance, extension marker.
3. **tokendog-mcp-toolkit** — Python (then TS) libraries for MCP authors: deferred loading, pagination, truncation, batching, dense schemas.
4. **tokendog-gate** — OPTIONAL Rust transport proxy: JSON compression, cache pooling, dedup, routing, budgets, real `usage.*` capture.
5. **tokendog-docs + benchmarks** — mkdocs site covering all 47 items; reproducible cost/accuracy benchmark harness.

Design principles (unchanged from brief): any single layer is useful standalone · extensions never fork the core (4 extension points) · static routing not LLM triage · measure before optimize · prefix stability is free money.

## 5 · Build sequence (decomposition into implementation plans)

The product is too large for one plan. It decomposes into ordered slices; **each slice gets its own writing-plans plan + implementation + verification** before the next. Ordering follows "measure before optimize" and "plugin-first."

- **Slice 1 — Measurement spine.** Repo scaffold + `token_count.py` telemetry hook (→ `~/.tokendog/telemetry/*.jsonl`) + `tokendog-cost` local MCP (SQLite, pluggable backend Protocol) + `/tokendog:cost` command + `tokendog-doctor`. *Exit: run a real session, see your own spend.* (brief items 32, 34, 35)
- **Slice 2 — Frugal behavior + guardrails.** `tokendog-frugal` always-on skill, `tokendog-hygiene` skill, remaining hook scripts (truncate_output, budget_enforce, budget_alert, idle_cleanup, mcp_hygiene_scan, mcp_response_cache, session_summary, skills_audit), `/tokendog:audit`, `/tokendog:budget`, `/tokendog:warmup`. (items 1–4, 8, 10, 14p, 16, 29, 30, 36p, 37, 40, 41, 42, 43, 44, 45)
- **Slice 3 — Templates.** Canonical `CLAUDE.md.template` + `settings.json.template` + `mcp-hygiene.md` + example-extension. (items 6, 22, 23, 38, 39, 46)
- **Slice 4 — MCP toolkit (Python).** deferred, pagination, truncate, batch, schema helpers + pattern docs; TS mirror later. (items 5, 7, 9, 15)
- **Slice 5 — Docs + benchmarks + v0.1 release.** mkdocs `how-it-works.md` (all 47 items), benchmark harness (with/without TokenDog), comparison vs Headroom.
- **Slice 6 — Gate MVP (Rust).** proxy skeleton → SmartCrusher-style JSON compression (11) → `prompt_cache_key` pooling (20) → prefix stability + deterministic ordering (17–18) → context dedup (13) → real usage capture (33) → server-side budgets (36).
- **Slice 7 — Advanced gate.** CCR reversible compress-retrieve (12), server truncation (14), session persistence (21), routing extensions (24, 27, 28), Memory API (47).

Every one of the 47 brief items lands in exactly one slice; none dropped.

## 6 · Testing & verification

- Python: `pytest` per script/module; each hook script independently testable with fixture stdin.
- Each slice has an explicit exit criterion (above) verified before moving on — evidence, not assertion.
- Slice 5 benchmark harness provides the headline %-savings claim empirically.
- Rust gate: `cargo test` per middleware layer; benchmarks in `tokendog-gate/benchmarks`.

## 7 · Glitch interoperability (first-class requirement)

Glitch (aka "iCode") is a **second token-spending runtime** — a multi-agent pipeline orchestrator with its own MCP server and a `.glitch/firmware/firmware.db` code-intelligence + memory store. The brief scoped TokenDog to "Claude Code **or Glitch"; TokenDog must reduce and attribute Glitch's spend, not just Claude Code's.

**What Glitch already provides** (do NOT duplicate): `files` index with per-file `summary`/`tokens`/`accesses` (it sends summaries, not full files) · `edges`/`clusters` code graph · `memory_index` with embedding-based semantic memory (`namespace`, `confidence`, `embedding`) · `context_log`/`memory_retrieval_log` usage logs · **agent-lifecycle hooks** (`post-tool-use`, `stop`, `stop-validators`) with an env-var contract, where the `stop` hook exposes **authoritative** `TOKENS_INPUT`/`TOKENS_OUTPUT`/`MODEL`/`AGENT_NAME`/`DURATION_SECONDS`/`COMPLETION_STATUS` (Claude Code hooks give NO token counts — Glitch's are better) · an existing telemetry endpoint `POST /api/v1/telemetry` (events `agent_invocation`, `agent_completion`). Glitch's hooks run under "Claude Code / OpenCode" — Glitch orchestrates agents on those runtimes.

**Posture: complementary, not duplicative.** Integration by layer:

| Layer | Coverage of Glitch | Mechanism |
|---|---|---|
| **gate** | ✅ Primary bridge | Runtime-agnostic proxy. Set Glitch `ANTHROPIC_BASE_URL` → tokendog-gate. All Glitch calls get compression, cache pooling, usage capture, budgets. Zero Glitch code changes. |
| **cache pooling (20)** | ✅ Highest synergy | Pool `prompt_cache_key` per Glitch pipeline-run so N spawned agents share the common-prefix cache. |
| **mcp-toolkit (5,7)** | ✅ | Apply deferred loading + dense schemas to Glitch's ~27-tool MCP to cut fixed-prompt cost. |
| **telemetry (32/33)** | ✅ | Cost MCP ingests Glitch `firmware.db` (`context_log`, `files.tokens`) + run artifacts for per-pipeline/run/agent/cluster attribution. |
| **memory (47)** | ✅ Defer to Glitch | Integrate with Glitch `memory_index`; do not run a competing memory system. |
| **hooks / telemetry (32)** | ✅ **Glitch has native hooks** | Glitch has agent-lifecycle hooks (`post-tool-use`, `stop`). TokenDog ships a Glitch `stop` hook that reads env vars → `TokenEvent(runtime="glitch")` with **authoritative** `TOKENS_INPUT`/`TOKENS_OUTPUT`/`MODEL` — no tiktoken approximation needed for Glitch. Must coexist with Glitch's existing `POST /api/v1/telemetry` (no double-count: either mirror to the local sink or read from the registry). |
| **skills / slash-commands** | ⚠️ Claude Code only | Skills and `/commands` are Claude Code UI surfaces. Glitch's equivalent is pipeline/agent YAML config — a separate adapter, not the plugin. This is the *only* genuinely Claude-Code-only layer. |

**Design changes forced by Glitch support:**
1. **Runtime abstraction** — `runtime ∈ {claude-code, glitch}` is first-class; shared spine (gate + telemetry + toolkit) with per-runtime surface adapters.
2. **TokenEvent schema** gains `runtime`, `pipeline`, `run_id`, `agent`, `cluster`.
3. **Single source of truth for usage** — the gate is authoritative for token counts; Glitch logs supply attribution metadata only (no double-counting).
4. **Cost-MCP backend** gains a Glitch ingestion adapter reading `firmware.db`.
5. **Item 47** reframed: integrate with Glitch memory, not build new.
6. **5th extension point:** a **runtime adapter** interface (alongside the brief's four).
7. **Glitch-native telemetry hook** — TokenDog ships a `stop` hook script for Glitch's `examples/hooks/`-style layout that emits a `TokenEvent(runtime="glitch")` with authoritative token counts from env vars. For Glitch, telemetry is Path-C quality (real `usage`) at the hook layer *without* the gate; the gate remains the option for compression/pooling.

**Build-sequence impact:** Slice 1 (telemetry) adds `runtime`/pipeline fields to TokenEvent from the start + a Glitch `firmware.db` ingestion adapter. Slice 6 (gate) is validated with a real Glitch call routed through it, and cache pooling is tested against a multi-agent Glitch pipeline. Slice 7 (memory) integrates with Glitch `memory_index` instead of building new.

## 8 · Out of scope (for now)

- TS mirror of the MCP toolkit (after Python proven).
- Self-hosted/fine-tuned backing model (item 26) — extension point only.
- Any embedding/semantic ("neural") feature — deferred; not part of v1.0 identity.
