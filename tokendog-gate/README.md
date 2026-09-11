# tokendog-gate

**Status: experimental, and not wired to anything.** There is no proxy; nothing in the plugin or
the Python package calls this crate. Before wiring it, read the measurement below.

## Why it is not wired

The transforms here rewrite the request body. On a real agent workload that is the wrong lever:

- Every metered turn already wrote to the prompt cache incrementally; write:read fell from 28.6% to
  1.0% over a session. The cache is doing its job.
- `compress` and `dedup` change bytes inside the cached prefix, which invalidates it. On the measured
  workload (cache read is 56–62% of cost, at 0.1× input price) the re-write cost exceeds anything
  the smaller body saves.
- The 5-minute pooled `cache_key` is worse than the 1-hour TTL the host already uses: the median
  inter-turn gap was 3 s, but 110 gaps of 5–60 min would each have rebuilt the prefix, at about $173
  more over the sample.

So for a cache-dominated workload — which every long agent session is — this crate would have cost
money. It stays as tested code for a workload where that is not true (no caching, or short
stateless calls). Nothing here affects what the plugin does today.

## Transforms

All `cargo test`-covered:

- `compress::crush` — cap long arrays + truncate long strings in JSON tool payloads.
- `caching::canonicalize` / `canonical_string` — byte-identical prefix for cache hits.
- `caching::cache_key` — pooled `prompt_cache_key` so a team shares the 5-min cache window.
- `dedup::dedup_contents` — drop repeated context.
- `observe::parse_usage` — authoritative `usage.*` from the API response.
- `observe::over_budget` — server-side hard budget.

## CLI

```bash
echo '{"messages":[...]}' | cargo run --quiet
```

Applies crush + canonicalize to a request body (what the proxy does before forwarding).

## Follow-up: live proxy (not in this slice)

Wiring an async HTTPS proxy (terminate TLS, forward to `api.anthropic.com`, stream the response,
capture `usage.*`, enforce budgets) is the next step. It needs a live API to test and is intentionally
left for manual validation — the transforms above are the tested core it will compose.

## Advanced transforms (Slice 7)
- `ccr::Ccr` — reversible compress-cache-retrieve: hand the model a compressed value + token, restore
  the original on demand via `retrieve` (in-memory store).
- `truncate::head_tail` — keep first + last N lines, drop the middle (stack traces live at the ends).
- `session::save` / `load` — serialize the cache so it survives a restart.
- `qa_cache::QaCache` — normalized exact-match question→answer cache.

## Deferred (follow-up — need network or a model, so out of the tested core)
- **SQLite-backed CCR** — persist the retrieve store to disk (currently in-memory).
- **Embedding-based semantic Q→A cache** — similarity match instead of exact (needs an embedding model;
  the project deliberately avoids unearned "neural" claims).
- **Local Ollama offload** (routing) — shell out to a local model for classification/summarization.
- **Anthropic Memory API** — persist facts across sessions (also available via Glitch's memory on that runtime).
- **Live HTTPS proxy** — terminate TLS, forward to api.anthropic.com, stream + capture usage + enforce budgets.
