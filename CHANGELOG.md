# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Initial development is complete across all layers; a tagged release has not yet been published.

### Added

- **In-session unused-connector notice (per project).** A SessionStart hook names MCP connectors that are never called IN THIS REPO and carry schema on every turn, with the reversible disable offered so the assistant runs it on your "yes" — no command to type, no auto-edit. Per-project by design: a connector idle in one repo may be used in another (a connector can be dead weight in one repo and called dozens of times in another). Once/day, opt out with `TOKENDOG_ADVICE=0`.
- **`save_inventory` now merges instead of replacing** — a failed or partial re-probe (a server that couldn't start, a node binary missing from a lean PATH) no longer erases a previously-measured schema size. New sizes win per tool; un-reprobed connectors keep theirs. The auto-refresh also prepends common package-manager bin dirs so node-based MCP servers probe.
- **Auto-measures MCP schema sizes — no command to remember.** A SessionStart hook runs `surface --refresh` in the background when the inventory is missing, stale (>7d), or a connector changed, so the statusline baseline and `tokendog floor` populate on their own. Detached (never blocks the session), throttled (≤ once/12h), writes only the inventory cache (no config change), opt out with `TOKENDOG_AUTO_REFRESH=0`. Removes the one manual step the baseline needed.
- **`TOKENDOG_QUIET` silences the end-of-turn Stop summary**, and **`TOKENDOG_STATUSLINE_FLOOR=1`** adds an opt-in floor segment to the statusline (the always-on MCP+skill+instruction baseline, by size, cached by config mtime). The statusline still shows only total ctx by default: Claude Code's payload carries no per-source breakdown of the live window, so the floor (a small static baseline) is the one source split the statusline can honestly show — the bulk of ctx is conversation history, which `tokendog floor` and the session drilldown break down instead.
- **`tokendog floor` + a Floor dashboard tab — the context-floor budget.** Itemises everything that rides in EVERY turn before you type — each MCP tool schema, each skill's always-on frontmatter, each instruction file — with its per-turn size, whether it's actually used, the carried total (size × turns), and a verdict: ■ reclaim (resident, never used), △ trim (used but heavy), ✓ keep. Surfaces the invisible cost of a loaded-but-never-called MCP or a skill that charges its frontmatter every turn and is never invoked. Deterministic.
- **`tokendog discovery` + a Discovery dashboard tab — the grep-loop flag.** Classifies each session's tool calls as finding (grep/ls/find/cat, Read/Grep, git log) vs doing (edits, builds, tests, commits) and reports the ratio; flags sessions with 20+ acting calls at 60%+ discovery — the model rebuilding a codebase map it has no memory of, one search per turn. Deterministic, no LLM. Measured live at 60% overall, 48 of 124 sessions mostly-exploring.
- **`tokendog pipelines` + a Pipelines dashboard tab — Claude spend per Glitch pipeline, EXACT.** Glitch already writes `.glitch/runs/<run>.json` stamping each stage's Claude `--session-id` (which is the transcript tokendog prices), so no Glitch change is needed: `glitch_runs.py` reads the run files, maps session→pipeline/run/stage, and joins the per-session cost. A pipeline run stops being an anonymous `sdk-cli` session and becomes "chunk-embed-store, 13 stages, $97". Shows tokendog's list-price weight beside Glitch's own per-stage figure.
- **Sessions table is sortable and opens on most-recently-used.** New **Last used** column; the default order is last activity (what you just touched), and every column header re-sorts on click with a direction toggle. `last_ts`/`last_at` carry the key; the view-model still ranks by input internally so the verdict narrative and truncation keep the heaviest sessions.
- **One range control, no competing date ranges.** The range moved out of a pill row into a single top-right control on the tab bar — scope + a dropdown that both selects the range and displays its from→to dates (`all projects · 7d · Sep 2 → Sep 9`). It is global: every tab's data is scoped to it. Removed the masthead sparkline and the stamp's date range (both showed a different window than the selector, which was confusing); the stamp is now build metadata only.
- **Full modern refresh, colourblind-safe by construction.** Status no longer relies on a red/green pair: "healthy" is neutral (a ✓, not green), and severity escalates ✓ → △ → ▲ → ■ with amber/orange/vermilion — every status carries a SHAPE glyph + word, so it reads in pure monochrome and for any colour-vision type. The masthead LED is a hollow ring when healthy, a filled dot on alert (shape, not just hue). Manrope display face for the wordmark/headings/headline numbers, a two-tone Token·Dog wordmark, accent eyebrow ticks on section headings, and a live daily-input sparkline in the masthead. Signal-teal accent + single-hue occupancy ramp.
- **A distinct visual identity ("watchdog console").** Signature signal-teal accent (was the default blue) with a matching single-hue occupancy ramp, and a status **LED** in the masthead that glows the worst live severity (green→amber→red, pulsing on critical) — the header is the alarm. Keeps the mono-forward numerics.
- **Exact PR attribution via a commit trailer + `tokendog install-hook`.** A `prepare-commit-msg` hook copies `CLAUDE_CODE_SESSION_ID` (which equals the transcript's sessionId) into a `Claude-Session:` trailer, so a commit names the exact session that made it — no time/file heuristic. `outcomes` prefers the trailer (labelled `exact`) and falls back to the heuristic (`guess`) only for un-stamped commits; the CLI and the Outcomes tab show which. The installer refuses to clobber a foreign hook, is reversible (`--remove`), and is inert on hand commits.
- **`tokendog errors` + a Tool-errors dashboard tab.** Per-tool call/error counts and the dominant failure category (timeout / not-found / permission / network / rate-limit / syntax / interrupted / nonzero-exit), matched by pattern — no LLM. Flags tools with 5+ calls failing 20%+. A failing tool bills twice (the failure, then the bigger-context retry), so this is real spend. tuneloop reads these with a model; tokendog stays deterministic and free.
- **A Cost tab** — the four billing buckets (cache write / read / output / fresh) by model, plus per-tool payload volume, lazily priced when the tab opens.
- **Dashboard parity: an Outcomes tab and a Burn column.** The outcomes report is now in the dashboard as its own tab, loaded lazily (it shells out to git, so it runs only when opened, cached per range, with a checkbox to resolve squash-merge PRs via `gh`). The sessions table gained a `Burn` (tokens/min) column — the runaway-vs-grinder signal that was previously CLI-only. Goal: the dashboard shows what the CLI does, since that is what people actually open.
- **Outcomes attribution is scoped to the machine owner's commits by default.** A shared clone's history holds everyone's commits; without a filter a teammate's commit landing in a local session's window on a same-named file would be mis-attributed. `git_commits` now filters to this clone's `user.email` (opt out with `--all-authors`), and both the CLI and the tab state that attribution is this-machine-only — a PR co-authored across laptops shows only the share done here. Reliable cross-machine attribution needs an explicit session-id commit trailer.
- **`tokendog outcomes` — cost per merged PR (ROI attribution).** Links each session to the commits that landed in its window in the same repo with overlapping files, and each commit to a PR (from the merge-commit subject, or via `--gh` when a squash-merge left no local merge commit — GitHub API, never Claude tokens). Reuses the existing per-session cost, so it is a join, not a new measurement. Honest about being heuristic: a PR fed by several sessions is marked as having SHARED cost, and unlinked sessions are reported, not hidden. This is the one tuneloop capability tokendog lacked; unlike tuneloop it needs no LLM enrichment.
- **`--since` / `--until` on every turn-measuring report** (`bands`, `resumes`, `coldstart`,
  `hygiene`, `surface`, `session`), in a new `window.py`. Until now the detectors could only
  answer "what is expensive across my history"; a rate limit is consumed in minutes, so the
  question a reader actually has — what happened between 17:29 and 17:45 — could not be put to
  the tool at all. Accepts local clock times, dates, ISO instants and spans back from now
  (`90m`, `3h`, `2d`), plus `today` / `yesterday` / `now`. `cost` keeps its own older
  `--since`/`--until`, which are dates for the SQL roll-up and are deliberately left alone.
  A clock time still ahead of now is read as yesterday's, so a window across midnight is not
  silently empty.
- **`hygiene`: burn rate, subagent roll-up and the billing-bucket split.** `Burn` (input tokens
  per minute) separates a runaway from a grinder carrying the same total. `Subs` credits a
  fan-out to the session that spawned it — subagents keep their own rows, since their contexts
  and their excess are their own, but a parent no longer reads as a dozen unrelated small
  sessions. A new "Where the weight is" table splits each session into cache read / 5m write /
  1h write / fresh and prices them, because `input` treats every token as one token and the bill
  does not: 600k read from cache and 600k written to a 1-hour cache differ twentyfold.
- **The session drilldown page is now chronological and range-aware.** Turns render in time order by default (a `chronological ⇄ costliest first` toggle keeps the old ranking), so row two follows row one and consecutive turns can be compared — previously it showed only the carried-ranked list, which jumped around in time. Opening a session from the dashboard carries the selected range through as a window: out-of-window turns are dimmed, a banner shows what the slice cost, and the back-link and JSON link return to the same range. The `/session/` route accepts `range` and threads it into `session_detail`.
- Time windows scope TURNS, not sessions, and a session's age and idleness are still measured
  over its whole life — reporting a three-day-old session as "open for 16 minutes" because that
  is all the window saw would hide exactly what the report exists to find.

### Fixed

- **A session was classed headless on the strength of a handful of records.** `hygiene` OR'd
  `is_headless` across every turn, so one `sdk-cli` record among thousands of `cli` ones flipped
  the whole session — and headless suppresses every age and idle verdict, so the one session
  actually worth closing was told to pre-load its context instead of being cleared. Observed at
  87 `sdk-cli` records against 2,025 `cli` in a live 610k-context session. Now decided by
  majority, with an even split reading as interactive (the direction where wrong advice is
  harmless).
- **Every 8-character subagent label collided.** `agent-` consumes six of the eight, leaving two
  hex digits, and one fan-out produces dozens: on one machine sixteen labels covered 376
  transcripts, 18 to 35 rows each, all rendered as the same handful of names. Subagent rows now
  take eight characters from after the prefix.
- **A fan-out whose parent was idle in the window lost its parent entirely.** The roll-up
  skipped a session with no in-window row, leaving orphan subagent rows with nothing tying them
  together — which is the case most worth pointing at. The parent session id is now recorded on
  the children regardless.
- **The windowed `hygiene` table disagreed with its own header.** The row filter that keeps the
  all-time table short (drop the healthy rows) also applied inside a named window, so a header
  reading "2 sessions" sat above one row. All-time still ranks by excess — what is worth fixing;
  a window now ranks by what was actually spent, since a session that carried 400k for twenty
  minutes has no excess if it stayed under the line.
- **An empty windowed report sent the reader to `tokendog doctor`.** Nothing happening in the
  sixteen minutes they asked about is an answer, not a broken install.

- **Turn counting: one API response, not one record.** A response is written to the transcript as
  one record per content block (thinking, text, tool_use), each repeating the same `message.usage`.
  Reading every record counted a single response 1-3 times, inflating turns and input tokens by
  ~2x (3,749 records against 1,860 responses in one session). `read_transcript` now groups on
  `requestId` and keeps the last record of each response, because `output_tokens` streams — early
  records carry partial counts and only the last carries the total. Distributions were largely
  unaffected (every record of a response shares one context size), but absolute totals were not:
  the measured figures in README.md and docs/FEATURES.md have been re-derived.

### Added

- **Connector probing** (`tokendog surface --refresh`) — starts each enabled connector, reads its
  tool list and sizes every schema, then caches the result to `~/.tokendog/surface.json`. The only
  command in tokendog that launches anything: probes run one at a time (concurrent credential
  prompts are unanswerable), under a per-connector timeout, with the server's own stderr captured
  rather than inherited, and every failure reported as a reason rather than raised. An unreachable
  connector is recorded as unreachable — which is itself worth knowing, since it is still costing
  its definitions.
- **Turning connectors off** (`tokendog surface --disable NAME [--apply]`) — dry run by default,
  timestamped backup before any write, the removed configuration stashed so `--enable` restores it
  verbatim, and a refusal to disable a connector with recorded calls unless `--force` is given.
  There is no automatic mode: a report recommends, a person decides.
- **`--json` on `surface`** — the raw view-model, for anything that wants to act on it.
- **Dashboard tabs and ranges** — Overview / Connectors / Recommendations / Findings / Detail, a
  7d / 14d / 30d / 90d / All selector carried in the URL so a view can be shared, and every
  headline shown against the previous window of the same length. Each range is ingested once and
  cached, and an out-of-range `days` snaps to an offered one rather than walking the tree for an
  unbounded span.
- **Recommendations** — one ranked list drawn from every detector, ordered by tokens involved and
  labelled with the effort each costs, because a config edit and losing your working state are not
  the same price.
- **Turn-by-turn drilldown** (`tokendog session <id>`, `--json`, and `/session/<id>` on the
  dashboard; session ids in the table link to it). "285 turns, 74.9M input" is a verdict with no
  explanation. This opens one session and says what entered the window at each turn, what it cost
  after it arrived — its size times the turns that then re-read it, stopping at the next reset —
  and which tool brought it. Also splits the floor every turn pays: tool schemas (from the surface
  inventory), always-on skills and instruction files from disk, and an explicitly-labelled
  remainder, because the API does not report what is inside a cached prefix and a finer split
  would be invention. Growth attributable to neither a tool result nor the previous reply is
  reported as a residual rather than assigned to the nearest category.
- **Masthead reduced to two type sizes.** It had five in one row — a 20px mark, an 11px wordmark,
  a 20px page title, a 12px scope and an 11.5px stamp — which read as unfinished. The product name
  is now the only heading; naming the page as well said the same thing twice.
- **One transcript can hold many contexts, and the reports now say so.** A reset does not close
  the file — it starts a fresh context inside it. Measured here: one transcript held ELEVEN,
  climbing 43k → 999k and being cleared, ten times over. A lifetime average therefore describes
  nothing that still exists, and a session compacted an hour ago was being reported as though it
  had never been. Turns are now segmented at each reset; the verdict reads the LIVE segment, and
  the dashboard shows `Ctx now` beside `Ctx in window`.
- **Calendar ranges in the reader's own timezone** — Today and Yesterday alongside 7d/14d/30d/90d/All.
  A calendar range is bounded by local midnight rather than being the last 24 hours, because
  "today" means the reader's day. The comparison window is always the same length immediately
  before, so Today compares against Yesterday.
- **A plain reload now shows current numbers.** The cache is reused only while the transcript tree
  is unchanged, checked by a stat sweep (file count + newest mtime) that costs milliseconds
  because it never opens a file. Explicit refresh still forces the work.
- **Context surface** (`tokendog surface`) — what is in the window before the conversation
  starts, and whether it earned its place. Grouped by CONNECTOR rather than by tool, because a
  connector is the unit you can switch off: an MCP server contributes all of its tools or none.
  Reports, per connector, its scope and enablement, which of its tools were actually called and
  how often, how many turns it was resident, and calls per 1,000 resident turns — then names the
  disable candidates. Also prices the always-on local surface (skills split into always-on
  frontmatter vs on-invoke body, plugin commands, instruction files). Reads config and disk only;
  it never connects to a server. Tool schema sizes are not in the transcript, so token columns
  stay blank until an inventory is captured, and residency is documented as approximate because
  nothing records which connectors a PAST session had.
- **`tools` on TokenEvent** — the tool names a response invoked, unioned across the records of
  that response (its content blocks are written separately, so keeping only the last record
  reported only the last tool). This is the only place a transcript says WHICH tool ran: the
  existing `tool` field is set by hooks, which cover only sessions started after the plugin was
  installed — 20% of turns on the machine this was built against.
- **Local dashboard** (`tokendog serve`) — a read-only page over the same figures the CLI
  reports: spend by window occupancy, a daily trend, a per-session action list, and the
  findings below. Standard-library HTTP only, loopback-bound, no web framework and no JS
  toolchain. `src/tokendog/views.py` is the shared view-model, and doubles as the export
  contract so a second surface renders these definitions rather than re-deriving them.
- **Statusline** (`tokendog-plugin/scripts/statusline.py`) — window occupancy, session age
  and the 5-hour / 7-day limit percentages, on screen while you work, with one nudge when a
  threshold trips. Thresholds are absolute token counts, not percentages of the window.
  `TOKENDOG_STATUSLINE_TAG` sets or removes the brand mark.
- **Resumes at the wall** (`tokendog resumes`) — a pause of 30+ minutes followed by a turn
  carrying 400k+, i.e. a window carried past the natural moment to reset it. Reports the
  escalation across a session and the tokens carried past each decision point.
- **Cold-start duplication** (`tokendog coldstart`) — several non-interactive runs against
  one project, each starting empty and re-discovering the same material. Prices the repeated
  climb, and names the files more than one run touched where hook telemetry exists.
- **`tokendog init` installs the statusline** — the `statusLine` entry is rendered with this
  checkout's absolute path at install time, because a static template cannot know where the repo
  lives and `${CLAUDE_PLUGIN_ROOT}` is expanded for hooks, not here. A settings file that already
  names a statusLine is left alone.
- **Session hygiene** (`tokendog hygiene`) — was a session ever managed, and what did drift
  cost? Reports growth, resets, and `excess`: the tokens a session carried ABOVE the 200k
  threshold, summed over its turns. Unlike a savings estimate this needs no counterfactual,
  because it only counts what was carried over a line the session could have held. Owns every
  occupancy / age / idle threshold and the severity rules, which `serve` now imports rather
  than keeping its own copy of.
- **Subagent transcripts are no longer told to close themselves** — an `agent-*` transcript's
  context ends when the subagent does, so age- and idle-based verdicts do not apply to it (the
  same reasoning already applied to headless runs). It is still measured for excess, since what
  it carried was real; the fix named is the delegation. Hygiene rows are also labelled by
  transcript rather than session id, because a subagent transcript carries its parent's
  sessionId and 104 rows were rendering under one label.
- **`entrypoint` on TokenEvent** — captured from the transcript and carried forward, with
  `is_headless`. A headless run and an interactive session need opposite advice: an exited
  run holds no window, so advising a reset for one is noise.


- **Measurement** — token telemetry event model, tiktoken-based approximation, JSONL sink, SQLite cost
  backend with grouped roll-ups, cost estimation, and a reporting CLI. Cross-runtime attribution
  (agent + Glitch) is built in.
- **Agent plugin** — a crash-proof telemetry hook, a `tokendog-cost` MCP server, and the slash
  commands `/tokendog:cost`, `:doctor`, `:budget`, `:audit`, `:init`.
- **Guardrails** — per-day/session budgets with a fail-open enforcement hook, output truncation, an
  end-of-session spend summary, and webhook budget alerts.
- **Frugal behavior** — always-on frugal and session-hygiene skills; a Glitch-native stop hook that
  records authoritative token counts.
- **Config templates** — idempotent `CLAUDE.md` and `settings.json` baselines installed via
  `tokendog init`, preserving org customizations below an extension marker.
- **MCP author toolkit** (`tokendog_mcp`) — pagination, response truncation, batch-query dedup, dense
  tool schemas, and deferred tool loading.
- **Docs & benchmarks** — an mkdocs site covering the full optimization framework and a reproducible
  token-savings benchmark harness.
- **Transport gate** (`tokendog-gate`, Rust) — JSON compression, canonical ordering + pooled cache
  keys, context dedup, usage parsing, budget checks, reversible compress-cache-retrieve, head+tail
  truncation, session persistence, and an exact-match Q→A cache.

### Deferred

- Live HTTPS proxy wiring, SQLite-backed CCR, embedding-based semantic cache, local model offload, and
  Anthropic Memory API integration (documented in `tokendog-gate/README.md`).
