# tokendog-gate

The optional TokenDog transport gate. This crate provides the token-optimization **transforms**
(all `cargo test`-covered):

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
