# TokenDog — Feature Inventory

Everything TokenDog implements today, grouped by layer, with the benefit and the
*kind* of each feature. This is the honest, complete map — nothing here is aspirational.

## How savings actually happen (two levers)

Token savings come from distinct mechanisms that hit different token buckets:

- **Lever A — Instruct (prompt-side, before generation).** Tell the model to be terse
  (no preamble, no "Please/Thank you"). The model *generates* fewer tokens. This is the
  **only** way to save on the output itself — once text is emitted it is already billed.
  Non-deterministic (the model may not fully comply).
- **Lever B — Trim/compact (post-processing, before the next turn).** Shorten content
  *after* it is produced, then store the shortened version. Saves **nothing** on the turn
  that just happened, but in an agent loop that output becomes **input/context on every
  later turn** — so it cuts input + cache-creation tokens going forward. Deterministic.
- **Transport pooling/caching (optional gate).** Canonical ordering, dedup, and pooled
  cache keys raise prompt-cache hit rate and drop duplicate payloads at the wire.

**Kind** column legend:

- **Passive** — cannot change model output; pure measurement.
- **Instruct** — guides the model (Lever A).
- **Active** — transforms a call (Lever B / transport).
- **Reference** — docs/config scaffolding.

TokenDog does **not** do semantic prose-rewriting or filler-word stripping of assistant
messages; that requires an embedding model and is explicitly deferred (see the last table).

---

## Layer 1 — Core Python package (`src/tokendog/`)

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| Telemetry event model (`event.py`) | `TokenEvent` schema with cross-runtime fields (runtime, pipeline, run_id, agent, cluster, tool, model) | One consistent record for both Claude Code & Glitch | Passive |
| Transcript reader (`transcripts.py`) | Reads **authoritative** per-turn `message.usage` from Claude Code transcripts, preserving the ephemeral 5m/1h cache split, `service_tier` and `inference_geo` | The cost path. One metered turn = one assistant record carrying `message.usage` | Passive |
| tiktoken approximation (`approx.py`) | Estimates tokens for any text | Sizing tool payloads only — **not** a billing path | Passive |
| JSONL sink (`sink.py`) | Appends events to a daily `.jsonl` file | Durable, greppable local audit trail | Passive |
| SQLite cost backend (`backend.py`) | Grouped roll-ups (by runtime / tool / session / model / source / tier / geo) | Fast "where do my tokens go?" queries | Passive |
| Pricing / cost (`pricing.py`) | Prices all four buckets per model: fresh input, output, cache read (0.1×), cache write (1.25× at 5m / 2× at 1h) | Cache is ~85–93% of a real agent bill; pricing it is the difference between a right and a wrong number | Passive |
| Ingestion (`ingest.py`) | Loads transcripts + the sink + Glitch `firmware.db` `context_log`; redacts file paths to basename at the emitter | Unified Claude + Glitch spend view, without leaking project/user/customer names | Passive |
| Reporting CLI (`report.py`) | `cost` `doctor` `budget` `audit` `savings` `init` | One command line for all insight | Passive |
| Budgets (`budget.py`) | Daily / session / alert limits + webhook | Spend guardrails (enforcement runs in the hook) | Passive |
| Truncation logic (`truncate.py`) | Head+tail cap of oversized text | Shrinks bloated tool output re-sent as context | Active (Lever B) — **off by default** |
| Savings ledger (`savings.py`) | Records every truncation (enforce vs shadow) to a separate ledger | The with/without comparison, per call | Passive |
| Templates (`templates.py`) | Idempotent CLAUDE.md / settings install with an extension marker | Drop-in frugal config that preserves your edits | Instruct |
| Config (`config.py`) | Resolves state dir (`~/.tokendog`, override `TOKENDOG_HOME`) | Isolatable, testable state | Passive |

## Layer 2 — Claude Code plugin (`tokendog-plugin/`)

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| `token_count` hook | Records **tool-payload volume** (`tool_payload_tokens`) on every Pre/Post tool use, Stop, SessionStart | Per-tool attribution. Deliberately not billable: those bytes are billed by the turn that carries them, so pricing them here would double-count | Passive |
| `truncate_output` hook | Runs the truncation logic per call; `off` (default) / `shadow` / `enforce` | Cuts context bloat; opt-in, watch-only first | Active (Lever B) — **off by default** |
| `budget_enforce` hook | PreToolUse deny when over budget; honors observe-only; fails open | Hard spend ceiling that never crashes a session | Active |
| `session_summary` hook | End-of-session spend recap | Per-session cost awareness | Passive |
| `budget_alert` hook | Webhook alert on threshold crossing | Team-level overspend notice | Passive |
| `tokendog-cost` MCP server | Exposes cost data to the agent | Ask "what have I spent?" in-session | Passive |
| `tokendog-frugal` skill | Always-on terseness guidance to the model | Fewer output tokens (Lever A) | Instruct |
| `tokendog-hygiene` skill | Session-hygiene practices (clear context, scope tools) | Avoids context bloat | Instruct |
| Slash commands | `/tokendog:cost` `:doctor` `:budget` `:audit` `:init` | Manual control surface | Passive |
| Glitch stop hook + `tokendog-stop.sh` | Records authoritative Glitch token counts; shell wrapper needs no Python edit | First-class Glitch support | Passive |

## Layer 3 — Config templates (`tokendog-templates/`)

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| `CLAUDE.md.template` | Frugal baseline below an extension marker (backs the `tokendog-frugal` skill) | Consistent org-wide frugality | Instruct |
| `settings.json.template` | Sane hook / permission defaults | One-command setup | Instruct |
| `mcp-hygiene.md` | Guidance to trim noisy MCP servers | Fewer wasted tool-schema tokens | Instruct |
| `example-extension` | Shows how to add org rules safely | Upgrades don't clobber your edits | Reference |

## Layer 4 — MCP author toolkit (`tokendog-mcp-toolkit/`)

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| `pagination` | Page large MCP results | Model pulls only what it needs | Active |
| `truncate` | Response-size caps for MCP tools | Bounded tool payloads | Active |
| `batch` | Dedup repeated queries | Avoids re-fetching identical data | Active |
| `schema` | Dense tool-schema helpers | Smaller tool definitions in context | Instruct/structural |
| `deferred` | Load tool schemas on demand | Cuts upfront tool-list token cost | Active |

## Layer 5 — Docs & benchmarks

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| `tokendog-docs/` | mkdocs site documenting the full 47-item framework | Reference for the whole method | Reference |
| `benchmarks/` | Reproducible token-savings harness | Prove savings claims locally | Passive |

## Layer 6 — Optional Rust transport gate (`tokendog-gate/`)

Only active if you run the gate as a proxy in front of the API.

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| `compress` | Structural JSON compression | Smaller request bodies | Active |
| `caching` | Canonical ordering + pooled cache keys | Higher prompt-cache hit rate across a team | Active |
| `dedup` | Removes duplicate context blocks | No repeated payloads | Active |
| `observe` | Parses real `usage` from responses | Authoritative counts at the wire | Passive |
| `ccr` | Context-cache retrieve / reuse | Reuse prior context | Active |
| `truncate` | Head+tail at transport | Wire-level size cap | Active |
| `session` | Session persistence | State across calls | Active |
| `qa_cache` | Exact-match Q→A cache | Skip re-asking identical prompts | Active |

## Deferred (documented, not built — need network / a model)

| Item | Why not yet |
|---|---|
| Live HTTPS proxy wiring | Needs real endpoint plumbing |
| SQLite-backed CCR | Persistence layer for the gate cache |
| Embedding-based semantic cache / NL filler-stripping | Needs an embedding model |
| Local model offload (Ollama) | Needs a local model |
| Anthropic Memory API integration | Needs the API |
| TypeScript MCP-toolkit mirror | Python-only for now |

---

## Safety posture — safe by default

A fresh install only measures and offers conservative guidance. Nothing silently
alters tool output. The features that can change what the model sees are gated:

| Feature | Effect | Default | Turn on with |
|---|---|---|---|
| `truncate_output` | Silently shortens oversized tool output (hard content loss) | **off** | `TOKENDOG_TRUNCATE_MODE=enforce` (or `shadow` to only measure) |
| `budget_enforce` | Denies calls once over budget | **off** (no limit set → never denies) | `/tokendog:budget --set-daily N` |
| `tokendog-frugal` / `tokendog-hygiene` skills | Bias the model toward frugal reads/searches (no content dropped) | **on** — conservative, quality-preserving guidance | (disable per Claude Code plugin/skill settings if unwanted) |

Run `tokendog doctor` to see the live state of the quality-affecting features.

`TOKENDOG_OBSERVE_ONLY=1` is an extra convenience switch: it forces truncation into
`shadow` (measure-only) and stops budget enforcement from denying — useful for a timed
"see the projected savings, change nothing" trial via `tokendog report savings`.
