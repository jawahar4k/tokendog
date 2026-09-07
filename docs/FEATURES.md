# TokenDog — Feature Inventory

Everything TokenDog implements today, grouped by layer, with the benefit and the
*kind* of each feature. This is the honest, complete map — nothing here is aspirational.

## Where the money actually goes (three levers)

Each lever below was sized against real transcripts rather than assumed. The figures are one
developer's history — 210 transcripts, 30,773 metered turns, 10.0B tokens, $8,409 — and a second
seat reported the same *shape* with different magnitudes (output 14.5% there, 9.0% here). Treat
the ordering as robust and the exact percentages as local: run `tokendog cost` and `tokendog bands`
on your own data before acting on any of it.

| Bucket | % of tokens | % of cost | Lever |
|---|--:|--:|---|
| Cache read | 96.6% | 56.5% | 1 — carry less |
| Cache write | 3.1% | 34.6% | 2 — rewrite less |
| Output | 0.3% | 9.0% | 3 — generate less |
| Fresh input | 0.0% | 0.0% | — |

The token and cost columns disagree violently, which is the whole point: cache reads are 96.6% of
the tokens at 0.1× the input rate, output is 0.3% of the tokens at 5× it. Counting tokens tells you
almost nothing about the bill.

**Lever 1 — Carry less (56.5% of cost).** Every token resident in the context window is re-read and
re-billed on *every subsequent turn*. One read is cheap; the multiplier is the turn count, and it
compounds silently. On Opus, carrying 1 MTok costs $0.50 per turn against $25 to generate 1 MTok
once — so **a token you carry for 50 turns costs more than a token you generate.** Measured here:
50.2% of turns run at ≥200k context and account for 82.4% of the bill. Served by output truncation
(what a turn stores is what later turns carry), `/clear` hygiene, session lifecycle, and `bands` to
locate where context accumulates.

**Lever 2 — Rewrite less (34.6%).** Cache *writes*, not reads. Anything that invalidates the cached
prefix makes you pay to rebuild it, at 1.25× the input rate for a 5-minute TTL and 2× for one hour.
On this data 1-hour writes are 33.0% of the bill and 5-minute writes 1.6% — which is why the
ephemeral 5m/1h split must survive ingestion rather than being collapsed into one scalar. Served by
canonical ordering, pooled cache keys, and dedup: a stable prefix is one you stop paying to rebuild.

**Lever 3 — Generate less (9.0%).** Telling the model to be terse. This is the only lever that
touches output at all — once text is emitted it is already billed — and it is non-deterministic, as
the model may not comply. It is worth doing. It is not worth doing first, and a framework that leads
with it is optimizing the smallest ninth of the bill.

**Fresh input rounds to zero (0.0%).** In a sustained agent loop essentially every input token is
either read from cache or written to it. Prompt-shrinking work pays off through Levers 1 and 2 —
by making the *carried* prefix smaller and more stable — not by reducing fresh input directly.

### Mechanisms

- **Instruct (prompt-side, before generation).** Tell the model to be terse. Serves Lever 3, and is
  the only mechanism that can touch output. Non-deterministic.
- **Trim/compact (post-processing, before the next turn).** Shorten content *after* it is produced,
  then store the shortened version. Saves nothing on the turn that just happened; in an agent loop
  that output becomes context on every later turn, so it serves Lever 1. Deterministic.
- **Transport pooling/caching (optional gate).** Canonical ordering, dedup, and pooled cache keys
  raise prompt-cache hit rate and stabilise the prefix. Serves Lever 2.

**Kind** column legend:

- **Passive** — cannot change model output; pure measurement.
- **Instruct** — guides the model (Lever 3).
- **Active** — transforms a call (Levers 1 and 2).
- **Reference** — docs/config scaffolding.

TokenDog does **not** do semantic prose-rewriting or filler-word stripping of assistant
messages; that requires an embedding model and is explicitly deferred (see the last table).

---

## Layer 1 — Core Python package (`src/tokendog/`)

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| Telemetry event model (`event.py`) | `TokenEvent` schema with cross-runtime fields (runtime, pipeline, run_id, agent, cluster, tool, model) | One consistent record for both Claude Code & Glitch | Passive |
| Project attribution (`transcripts.py`) | Derives `project` from each turn's recorded `cwd`, as a basename | `~/.claude/projects` holds every project on the machine; without this a roll-up answers "what did this laptop spend". Basename only — a full path leaks the home directory and often a client name. Absent `cwd` means unknown, never a guess | Passive |
| Transcript reader (`transcripts.py`) | Reads **authoritative** per-turn `message.usage` from Claude Code transcripts, preserving the ephemeral 5m/1h cache split, `service_tier` and `inference_geo` | The cost path. One metered turn = one assistant record carrying `message.usage` | Passive |
| tiktoken approximation (`approx.py`) | Estimates tokens for any text | Sizing tool payloads only — **not** a billing path | Passive |
| JSONL sink (`sink.py`) | Appends events to a daily `.jsonl` file, with a per-day size cap (`TOKENDOG_MAX_SINK_MB`, default 64) and a retention window (`TOKENDOG_RETENTION_DAYS`, default 30); records every failed write to a health file | Durable, greppable local audit trail that cannot grow without bound or stop silently | Passive |
| SQLite cost backend (`backend.py`) | Grouped roll-ups (by runtime / tool / session / model / source / tier / geo) | Fast "where do my tokens go?" queries | Passive |
| Pricing / cost (`pricing.py`) | Prices all four buckets per model: fresh input, output, cache read (0.1×), cache write (1.25× at 5m / 2× at 1h) | Cache is ~85–93% of a real agent bill; pricing it is the difference between a right and a wrong number | Passive |
| Ingestion (`ingest.py`) | Loads transcripts + the sink + Glitch `firmware.db` `context_log`; redacts file paths to basename at the emitter | Unified Claude + Glitch spend view, without leaking project/user/customer names | Passive |
| Context bands (`bands.py`) | Buckets every metered turn by context size (cache read + cache write + fresh input), reports concentration and peak context per transcript | Answers "how big was the context when it was spent" — the dimension runtime/tool/session/model cannot express | Passive |
| Reporting CLI (`report.py`) | `cost` `doctor` `budget` `audit` `savings` `bands` `init` | One command line for all insight | Passive |
| Budgets (`budget.py`) | Daily / session / alert limits + webhook | Spend guardrails (enforcement runs in the hook) | Passive |
| Truncation logic (`truncate.py`) | Head+tail cap of oversized text | Shrinks bloated tool output re-sent as context | Active (Lever 1) — **off by default** |
| Savings ledger (`savings.py`) | Records every truncation (enforce vs shadow) to a separate ledger | The with/without comparison, per call | Passive |
| Templates (`templates.py`) | Idempotent CLAUDE.md / settings install with an extension marker | Drop-in frugal config that preserves your edits | Instruct |
| Config (`config.py`) | Resolves state dir (`~/.tokendog`, override `TOKENDOG_HOME`) | Isolatable, testable state | Passive |

## Layer 2 — Claude Code plugin (`tokendog-plugin/`)

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| `token_count` hook | Records **tool-payload volume** (`tool_payload_tokens`) on every Pre/Post tool use, Stop, SessionStart | Per-tool attribution. Deliberately not billable: those bytes are billed by the turn that carries them, so pricing them here would double-count | Passive |
| `truncate_output` hook | Runs the truncation logic per call; `off` (default) / `shadow` / `enforce` | Cuts context bloat; opt-in, watch-only first | Active (Lever 1) — **off by default** |
| `budget_enforce` hook | PreToolUse deny when over budget; honors observe-only; fails open | Hard spend ceiling that never crashes a session | Active |
| `session_summary` hook | End-of-session spend recap | Per-session cost awareness | Passive |
| `budget_alert` hook | Webhook alert on threshold crossing | Team-level overspend notice | Passive |
| `sink_health` hook | SessionStart warning when the sink has stopped accepting writes, **or when no interpreter on the machine can import `tokendog`** | The other hooks swallow every exception so they never crash a session; this is the one place a broken sink — or a plugin recording nothing at all — is said out loud | Passive |
| Interpreter bootstrap (`_bootstrap.py`) | Re-execs hooks and the MCP server under a Python that can import `tokendog` (`TOKENDOG_PYTHON`, `$VIRTUAL_ENV`, a project `.venv/`, the `tokendog` console script), caching the result in `~/.tokendog/interpreter` | `python3` is rarely the env pip installed into. Without this the ImportError is swallowed and the plugin silently records nothing; costs one probe, once, and only after the default interpreter has already failed | Passive |
| `tokendog-cost` MCP server | Exposes cost data to the agent | Ask "what have I spent?" in-session | Passive |
| `tokendog-frugal` skill | Always-on terseness guidance to the model | Fewer output tokens — the 9% lever (Lever 3) | Instruct |
| `tokendog-hygiene` skill | Session-hygiene practices (clear context, scope tools) | Avoids context bloat | Instruct |
| Slash commands | `/tokendog:cost` `:doctor` `:budget` `:bands` `:audit` `:init` | Manual control surface | Passive |
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

Only active if you run the gate as a proxy in front of the API. Most of it targets Lever 2 —
the 34.6% of the bill spent rewriting a prefix that did not need to change.

| Feature | What it does | Benefit | Kind |
|---|---|---|---|
| `compress` | Structural JSON compression | Smaller request bodies | Active |
| `caching` | Canonical ordering + pooled cache keys | Higher prompt-cache hit rate across a team — a stable prefix is one you stop paying to rebuild | Active (Lever 2) |
| `dedup` | Removes duplicate context blocks | No repeated payloads carried forward | Active (Levers 1, 2) |
| `observe` | Parses real `usage` from responses | Authoritative counts at the wire | Passive |
| `ccr` | Context-cache retrieve / reuse | Reuse prior context instead of re-writing it | Active (Lever 2) |
| `truncate` | Head+tail at transport | Wire-level size cap on what gets carried | Active (Lever 1) |
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
