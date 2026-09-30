# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.5] — 2026-09-30

### Fixed
- **The reload notice asked for a reload that would load nothing.** Hook scripts and the statusline
  are looked up on every call, so an update that changes only them is already live in every open
  window. The notice now compares what a session actually loads — hooks, commands, skills, agents,
  MCP servers and the manifest minus its version — against the previous cached version, and stays
  quiet when they match. Anything it cannot compare keeps the notice.

## [0.10.4] — 2026-09-30

### Fixed
- **The restart notice stayed up after `/reload-plugins`.** It compared the install time with the
  session's start, and a reload loads the update without restarting, so the start never moves.
  It now also reads when `/reload-plugins` last ran, from the transcript tail, and clears once a
  reload follows the install. It matches only the harness's own record of the command, never text
  that merely mentions it — an assistant reply easily can.
- The notice says `/reload-plugins to load it`: a reload keeps the session; a restart does not.

## [0.10.3] — 2026-09-30

### Added
- **`0.10.3 installed, restart to load it`.** The update notice only fired when the installed
  plugin was behind its source — which, once updated, it never is. But a window opened before the
  update does not load newly registered hooks until it restarts, and nothing on screen said so.
  The statusline now compares the install time with this session's start and says so per window.

### Changed
- The on-disk baseline shown before a session's first split uses the same format as setup:
  lowercase `k`, parts in brackets separated by commas.

## [0.10.2] — 2026-09-30

### Added
- **`→ stale, /clear` is back**, keyed on idleness rather than session age: hours since the
  transcript was last written, which on resume is when you left. It fires on the hygiene report's
  "Close it now" rule — idle 12h and still carrying 150k — pinned to the report's constants by a
  test. The old verb used session age, so it also fired on long sessions being actively worked.
  It outranks every other verb: a loaded session left idle is the cheapest fix there is.

## [0.10.1] — 2026-09-30

### Changed
- The line opens with `folder ⎇ branch`, then the model: across several windows, where you are
  is what tells them apart.

### Fixed — regressions from the 0.9.0 context bar
- **The bar coloured by percentage alone, which lies on a big window.** 365k is re-read every turn
  whether the window is 200k or 1M; on 1M it read 37% and green. It now takes the worse of
  fullness and absolute tokens carried, so 365k is yellow and 450k is red on any window.
- **No hint before a split was cached.** The first turns of a session — exactly when a resumed
  450k window most needs saying — showed nothing. Absolute occupancy decides until the split exists.
- **Setup lost its colour.** It turns yellow from 120k and red from 240k again: past those, only
  disabling something helps.

## [0.10.0] — 2026-09-30

### Added — session learnings
- **Capture** at `PreCompact` and `SessionEnd`, in a detached worker: new transcript bytes only,
  extract error→fix pairs, corrections and compaction summaries; prefilter (most sessions never
  reach a model); Haiku generator defaulting to `[]`; code gates (shape, evidence, cited paths
  must exist, dedupe, redaction, length); a Haiku critic that rejects by default. A failed critic
  keeps nothing, and a failed model call does not advance the offset, so those turns are retried.
- **Apply** at every `SessionStart`: shared lessons, then yours, as notes to check, capped.
- **Share**, off unless `TOKENDOG_LEARN_SHARE=on`: weekly, one PR per repo, built in a throwaway
  worktree, `git add -f` exactly the lesson files and never `_local/`.
- `/tokendog:learnings` — list, `--forget`, `--share-now`, `--index`. Statusline shows what the last
  capture did, and shows it when it failed.

### Found by running it on real sessions
- The instruction must be the prompt argument: given on stdin, the model treated it as possible
  injected content and refused. The captured signal travels inside explicit markers as data.
- Replies arrive in a code fence, and `paths` held non-paths; the parser and gates handle both.
- "An error, then a later success of the same tool" counted 96 fixes on one Bash-heavy session,
  most of them an error followed by an unrelated command. A Bash retry must now run the same
  program, and a non-zero exit whose output does not say what failed is not an error. 96 → 39.
- End to end on a real session: 5 candidates, all through the gates, 1 kept by the critic — a
  genuine esbuild pitfall with the build error quoted as evidence.

## [0.9.0] — 2026-09-30

### Changed — statusline, after use
- Reads `model · folder:branch · ctx 365k/1M ▓▓░░░░ 37% · setup 76k (sys 34k, mcp 21k, skills 14k,
  agents 6k) · cache · lessons · ≈$`.
- **Removed:** session age, the 5-hour and 7-day windows, and the work total. None of them changed
  what anyone did next. The work half still drives the `/clear or /compact` hint; it just is not
  printed.
- **Added:** a six-cell context bar, green under 50%, yellow under 80%, red above, with `/compact`
  from 80%; and a lessons segment that says what session learning last did in this repo, hidden
  where it never ran and visible when it failed.
- Setup parts sit in brackets separated by commas — the dot is the segment separator, and reusing
  it inside one segment read as four. Counts use a lowercase `k`; the branch joins with a colon.

## [0.8.0] — 2026-09-30

### Changed — condenser selection (port notes §5)
- **One selection rule replaces the three tiers.** Keeps the first 30 and last 60 lines verbatim,
  then every problem line (error, fail, warn, exception, traceback, denied, timeout, not found, ✗)
  with the three lines after it and up to 20 stack frames, inside a 5,000-token signal budget —
  which is what stops a log where every line says "error" from turning back into the input.
- Middle lines that differ only in numbers or hashes collapse to one line and `×N`; head and tail
  stay exact. Lines over 2,000 characters are clipped in the digest; the spill has them whole.
- **Floor is 8,000 tokens, not 220 lines.** Lines are the wrong unit.
- **Grep tool output is measured and never changed**, until fleet data says it is safe.
- `jq`, `git blame`, `git log -p` and `gh pr diff` join the commands that are never condensed.
- Every constant has an override (`TOKENDOG_CONDENSE_MIN_TOKENS` and friends); unset, non-numeric
  or negative means default, and 0 stays valid.

### Measured
- On one real workload the ported rules find almost nothing to do: in seven days one Bash command
  output crossed 8K tokens (p90 was 5K), while 390 file reads crossed 2K. The read guard, not the
  condenser, is the lever for that workload. `tokendog savings` now says which of those two
  reasons explains a zero, instead of claiming the condenser is off.

### Testing
- The file-read guard's tests used fixtures under the new floor, so they would have passed with
  the guard deleted. They are now over the floor; removing the guard fails 14 of them.

## [0.7.0] — 2026-09-30

### Added — statusline
- **Folder and branch**, `acme-api ⎇ main`. The folder is the basename, never the path. The branch
  comes from the payload or is read from `.git/HEAD` without running git, following a worktree's
  `gitdir:` pointer and showing a short SHA when detached. A branch name that is not plainly a ref
  is refused, since HEAD is writable by anyone with repo access and this prints to a terminal.
- **Cache state, silent when healthy.** Speaks only when the next turn will re-cache (`cache cold:
  next turn re-caches 285K`), when a large rebuild just happened, or in the last ten minutes
  before expiry.
- **Org spend limit** from `rate_limits.spend_limit`, from 75%; never dropped once shown.
- **Context band** on the ctx segment, pinned by test to `tokendog bands` so the two agree.
- **`≈$`**, because the figure is a list-price estimate, and whole dollars from $99.995.
- **Width fitting.** Each segment declares how it can shorten; reductions apply in one global
  order, least useful first, so the verb at the end of the line outlives the trivia in the middle.
  Width comes from an ancestor's tty, or `TOKENDOG_STATUSLINE_COLS` / `COLUMNS`; unknown width
  reduces nothing.
- **Update notice**, `v0.6.0→0.7.0 /plugin update`, only when the installed plugin is behind its
  marketplace source. Two local files, no network, cached for a minute. Versions are compared only
  like with like and refused if they are not plainly versions.

### Changed
- The statusline never throws: its last resort is the bare mark, not a traceback per keystroke.

## [0.6.0] — 2026-09-29

### Added
- **`tokendog settings`** — one contract for every key written into your
  `~/.claude/settings.json`. Shows the plan by default and changes nothing; `--apply` writes it,
  `--replace` takes over a key you set yourself, `--uninstall` removes only ours and restores
  exactly what was displaced. Every write is backed up and renamed into place atomically, and a
  settings file that does not parse is refused rather than reformatted.
- Ownership is a marker we wrote, compared with whitespace collapsed — never a substring match on
  the name, which would adopt a user's own `~/my-tokendog-statusline.sh` and delete it on
  uninstall. A key written by an older `init` is adopted by *resolved script path*, so existing
  installs upgrade instead of being reported as a stranger's.
- `status` separates `installed`, `drifted` (ours, points at another checkout), `broken` (ours,
  points at a script that is gone) and `other`. Broken outranks drifted: a command that prints
  nothing sends the reader hunting for the wrong problem.

## [0.5.0] — 2026-09-28

### Changed
- **The condenser saves before it cuts.** In enforce mode the full tool output is written to
  `~/.tokendog/output/<session>/` (0700 dirs, 0600 files, pruned after 7 days) and the digest ends
  with a pointer naming the exact line ranges that are missing, so any of them can be read back
  with `Read(offset=…, limit=…)`. If the spill cannot be written, nothing is cut — a cut with
  nowhere to read the rest back is the truncation this replaces.
- Every condense tier now reports the 1-based line ranges it kept, which is what makes the gaps
  nameable. A worker summary reports none, because no line of the original survives it.
- Shadow writes no spill: filling the disk with output nobody will read is not measuring.

## [0.4.0] — 2026-09-28

### Added
- **Read guard** (`tokendog readguard`, `tokendog.readguard`). A `PreToolUse` hook on `Read` that
  stops a large whole-file read *before* it enters the window, instead of cutting it afterwards.
  It charges the intervention its own cost: blocking spends one extra round trip that re-reads the
  window at cache-read rate, so it only pays while the context is under 40x the file's size. Off
  above that line, which is most of a long session. Shadow by default; a repeat read of the same
  file is always allowed.
- **`bulk-reader` subagent** — reads big files in its own throwaway window on Haiku and returns a
  summary with `file:line` references, so the file is billed once instead of on every later turn.
- The guard's ledger records allows as well as suggestions, because a guard that fires rarely and
  a guard that is broken look identical from a file containing only the times it fired. Numeric
  plus a file extension; never a path.

## [0.3.0] — 2026-09-27

### Added
- **The setup/work ledger** (`tokendog split`, `tokendog.ledger`). Divides the window by which
  lever moves it: setup (system prompt, connector schemas, skills, agents) shrinks only by
  disabling something and is re-injected by `/clear`; work (tool results, messages, thinking)
  shrinks only by `/clear` or `/compact`. Totals stay exact, from each turn's `usage`; only the
  split is estimated, and the session measures its own chars-per-token ratio rather than
  assuming 4.
- **Statusline shows the split** and names the lever that works: `/clear or /compact` when the
  history is heavy, "setup is heavy" when it is not. The old rule fired on occupancy, which
  nags at a big window that is fine and says nothing about a small one that is all dead schema.
- A Stop hook refreshes the split from the bytes added since the last turn: a 124 MB transcript
  costs ~660 ms once and ~0.1 ms per turn after.

## [0.2.2] — 2026-09-17

### Fixed
- The budget deny said only "budget exceeded". It now names the cap that tripped, and says that
  the daily figure resets at midnight while the session figure is the session's whole life and
  does not reset — a session cap, once crossed, stays crossed until it is raised. Waiting for a
  reset that was never coming was the actual user-facing failure.
- `by_tool` rows carry their connector, and the drawer and session page group an MCP server's
  tools under one header instead of listing twenty flat rows.
- Drawer tables no longer inherit the dashboard's 1080px minimum width, so they stopped growing
  horizontal scrollbars for two- and four-column tables. By tool moved above Turn by turn.

### Testing
- The suite is now hermetic against exported `TOKENDOG_*` switches. Running with
  `TOKENDOG_OBSERVE_ONLY=1` set (what you do while dogfooding) made every deny and enforce test
  pass by doing nothing.
- A server test pinned a fixed 2026-09-08 timestamp against the dashboard's 7-day default range,
  so it began failing once the calendar passed it. It is relative to the clock now.

## [0.2.1] — 2026-09-13

### Fixed
- Slash commands ran `python -m tokendog.report`, which failed on machines with only `python3`
  and on any `python3` that could not import tokendog. They now go through the same interpreter
  bootstrap as the hooks and say so if nothing works.
- The budget hook denied the very call that raises the cap, leaving no way out from inside a
  session. `/tokendog:budget` is now exempt, and the deny reason names both fixes.
- `--set-daily $1500` is read as 1500.

## [0.2.0] — 2026-09-12

First tagged release. Measurement is the product.

### Added
- Local dashboard (`tokendog serve`) with session drilldown, context floor, discovery ratio,
  tool errors, pipelines, outcomes, condenser savings.
- Intervention ledger (`tokendog effect`): did a change reduce tokens, by turn position.
- Tool-output condenser (opt-in, off by default): deterministic tiers for grep/test/log output;
  never cuts a file the model asked to read. `tokendog condense --replay` shows what it would drop
  on your real transcripts.
- Interpreter bootstrap: hooks re-exec under a Python that can import `tokendog`, so a bare
  `python3` no longer silently records nothing.
- Support for both `mcp` majors.

### Changed
- Cost is priced only from authoritative usage (transcripts, Glitch stop hook); hook events carry
  payload volume and are never priced. The 5m/1h cache-write split survives ingestion.
- Session verdicts judge the current window, not the session's age: a compacted session is not told
  to retire.
- Absolute paths are never published; project attribution is the `cwd` basename only.
- The Rust gate is documented as experimental and unwired, with the measurement that says why.

## [Unreleased]

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
