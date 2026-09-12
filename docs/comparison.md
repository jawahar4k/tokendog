# Comparison

## vs Headroom

Different jobs. Headroom compresses what the model reads, in a proxy between the agent and the API:
AST-level code compression, reversible retrieval, a hold-out of fresh reads from the prompt cache.
TokenDog measures what your sessions cost and tells you which ones to close; its own compression is
a small opt-in hook.

| Dimension | TokenDog | Headroom |
|---|---|---|
| Primary job | Measurement and session hygiene | Context compression |
| Runs as | Plugin hooks + local CLI/dashboard | Proxy (`ANTHROPIC_BASE_URL`) |
| Sees | Transcripts after the fact | Every request, both directions |
| Can rewrite earlier context | No | Yes |
| Compression | Opt-in condenser, never cuts a file read | AST, JSON, prose, reversible (CCR) |
| Cache-aware | Prices the TTL split; no transforms | Aligns prefixes; matures reads out of cache |
| Attribution | Project, model, entrypoint, pipeline, cost per PR | Per-request savings |
| Cost to run | None; no LLM calls | Proxy in the critical path |

If your bill is large tool payloads, Headroom's approach is the right one. If it is context carried
across long sessions — which is what one measured coding workload was — the win is in what TokenDog
points at, and compression is a third-order effect.

## vs tuneloop

tuneloop answers "was my AI spend worth it" (cost per merged PR, success rate, autonomy over time);
TokenDog answers "why did my limit just vanish, and what do I disable or reset". They overlap on
outcome attribution and tool-error surfacing, which TokenDog now covers deterministically.

| Dimension | TokenDog | tuneloop |
|---|---|---|
| Primary lens | Context economics / rate-limit forensics | Outcome / ROI, retrospective |
| Cost per merged PR | Yes (`outcomes`) | Yes |
| Spend per pipeline | Yes (`pipelines`, reads `.glitch/runs`) | — |
| Tool error rates | Yes (`errors`) | Yes |
| Time-window forensics (`--since`/`--until`), burn rate, occupancy bands | Yes | — |
| Exact attribution | Commit trailer (`install-hook`) — no LLM | LLM-enriched |
| Needs an LLM / its own token spend | **No** — deterministic, free, instant | Yes (~$ per run) |
| Actuator (turn a connector off) | Yes (`surface --disable`) | — |
| Harnesses | Claude Code (Glitch optional) | Claude Code, Codex, OpenCode, Pi |

For the rate-limit problem TokenDog is the closer fit and adds nothing to the bill —
every number comes from transcripts you already have. tuneloop adds multi-harness support and
LLM-derived qualitative themes (work type, complexity, re-steer patterns) that TokenDog deliberately
omits, since paying tokens to analyse a token limit is the wrong trade.
