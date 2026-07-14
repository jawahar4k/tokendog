# TokenDog Slice 7 — Advanced Gate (Rust) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extend the `tokendog_gate` crate with the advanced transforms — reversible compress-cache-retrieve (CCR), head+tail response truncation, cross-restart session persistence, and an exact-match Q→A cache — all `cargo test`-covered. Honestly document the pieces that need network/embeddings (Ollama offload, Anthropic Memory API, embedding-based semantic cache, SQLite-backed CCR) as follow-ups.

**Architecture:** Four new pure modules in `tokendog-gate/src/`, each unit-tested. CCR uses an in-memory store (SQLite persistence is a documented follow-up). The semantic Q→A cache is implemented as exact/normalized match — embedding-based similarity is deferred (it needs a model, matching the project's "no unearned neural" stance). Nothing here needs the network.

**Tech Stack:** Rust (stable 1.97), `serde_json`. Cargo at `$HOME/.cargo/bin/cargo`.

## Global Constraints

- Crate `tokendog_gate`; Apache-2.0; edition 2021; only dependency `serde_json`.
- Cargo invoked as `$HOME/.cargo/bin/cargo`; run from `tokendog-gate/`.
- Each task adds its `pub mod` line with its file, so the crate compiles at every step.
- No `Date`/random nondeterminism — CCR tokens use a monotonic counter, not time/random.
- Deferred features (Ollama item 27, Memory API item 47, embedding semantic cache, SQLite CCR) are documented in the README, NOT stubbed with fake implementations.
- Self-contained under `tokendog-gate/`; does not touch the Python suite.
- TDD via Rust `#[cfg(test)]`; commit after each green task.

---

### Task 1: Reversible compress-cache-retrieve (`ccr.rs`)

**Files:**
- Create: `tokendog-gate/src/ccr.rs`
- Modify: `tokendog-gate/src/lib.rs` (add `pub mod ccr;`)

**Interfaces:**
- Produces: `ccr::Ccr` with `new()`, `stash(&mut self, value: &Value, max_array: usize, max_string: usize) -> (Value, String)` (returns compressed value + a retrieval token, storing the original), and `retrieve(&self, token: &str) -> Option<&Value>`.

- [ ] **Step 1: Write the module WITH its failing test**

`tokendog-gate/src/ccr.rs`:
```rust
use crate::compress::crush;
use serde_json::Value;
use std::collections::HashMap;

/// Compress-Cache-Retrieve: hand the model a compressed value + a token; keep the original so a
/// later `retrieve` call can restore it on demand. (In-memory; SQLite persistence is a follow-up.)
pub struct Ccr {
    store: HashMap<String, Value>,
    counter: u64,
}

impl Ccr {
    pub fn new() -> Self {
        Ccr { store: HashMap::new(), counter: 0 }
    }

    pub fn stash(&mut self, value: &Value, max_array: usize, max_string: usize) -> (Value, String) {
        self.counter += 1;
        let token = format!("tokendog-ccr-{}", self.counter);
        self.store.insert(token.clone(), value.clone());
        (crush(value, max_array, max_string), token)
    }

    pub fn retrieve(&self, token: &str) -> Option<&Value> {
        self.store.get(token)
    }
}

impl Default for Ccr {
    fn default() -> Self { Self::new() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn stash_compresses_and_retrieve_restores() {
        let mut ccr = Ccr::new();
        let original = json!({"items": (0..100).collect::<Vec<i32>>()});
        let (compressed, token) = ccr.stash(&original, 10, 1000);
        // compressed is smaller
        assert!(compressed["items"].as_array().unwrap().len() < 100);
        // retrieve restores the exact original
        assert_eq!(ccr.retrieve(&token), Some(&original));
    }

    #[test]
    fn unknown_token_is_none() {
        let ccr = Ccr::new();
        assert!(ccr.retrieve("nope").is_none());
    }

    #[test]
    fn tokens_are_unique() {
        let mut ccr = Ccr::new();
        let (_, t1) = ccr.stash(&json!(1), 10, 10);
        let (_, t2) = ccr.stash(&json!(2), 10, 10);
        assert_ne!(t1, t2);
    }
}
```

Add to `tokendog-gate/src/lib.rs`:
```rust
pub mod ccr;
```

- [ ] **Step 2: Run the tests**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test ccr`
Expected: 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/src/ccr.rs tokendog-gate/src/lib.rs
git commit -m "feat: gate CCR reversible compress-cache-retrieve (in-memory)"
```

---

### Task 2: Head+tail response truncation (`truncate.rs`)

**Files:**
- Create: `tokendog-gate/src/truncate.rs`
- Modify: `tokendog-gate/src/lib.rs` (add `pub mod truncate;`)

**Interfaces:**
- Produces: `truncate::head_tail(text: &str, keep: usize) -> (String, bool)` — keeps the first `keep` and last `keep` lines, replacing the middle with an omitted-count marker. Returns `(text, was_truncated)`.

- [ ] **Step 1: Write the module WITH its failing test**

`tokendog-gate/src/truncate.rs`:
```rust
/// Keep the first and last `keep` lines; drop the middle (where stack traces rarely hide) with a marker.
pub fn head_tail(text: &str, keep: usize) -> (String, bool) {
    let lines: Vec<&str> = text.lines().collect();
    if keep == 0 || lines.len() <= keep * 2 {
        return (text.to_string(), false);
    }
    let head = &lines[..keep];
    let tail = &lines[lines.len() - keep..];
    let omitted = lines.len() - keep * 2;
    let mut out = head.join("\n");
    out.push_str(&format!("\n...[tokendog: {} lines omitted]...\n", omitted));
    out.push_str(&tail.join("\n"));
    (out, true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_head_and_tail() {
        let text = (0..1000).map(|i| i.to_string()).collect::<Vec<_>>().join("\n");
        let (out, cut) = head_tail(&text, 10);
        assert!(cut);
        let lines: Vec<&str> = out.lines().collect();
        assert_eq!(lines[0], "0");
        assert_eq!(lines[lines.len() - 1], "999");
        assert!(out.contains("omitted"));
    }

    #[test]
    fn short_text_unchanged() {
        let (out, cut) = head_tail("a\nb\nc", 10);
        assert_eq!(out, "a\nb\nc");
        assert!(!cut);
    }
}
```

Add to `tokendog-gate/src/lib.rs`:
```rust
pub mod truncate;
```

- [ ] **Step 2: Run the tests**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test truncate`
Expected: 2 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/src/truncate.rs tokendog-gate/src/lib.rs
git commit -m "feat: gate head+tail response truncation"
```

---

### Task 3: Session persistence (`session.rs`)

**Files:**
- Create: `tokendog-gate/src/session.rs`
- Modify: `tokendog-gate/src/lib.rs` (add `pub mod session;`)

**Interfaces:**
- Produces: `session::save(entries: &serde_json::Map<String, Value>) -> String` and `session::load(s: &str) -> serde_json::Map<String, Value>` — serialize/restore cache entries so the cache survives a restart. Corrupt input → empty map.

- [ ] **Step 1: Write the module WITH its failing test**

`tokendog-gate/src/session.rs`:
```rust
use serde_json::{Map, Value};

/// Serialize session cache entries to a string that survives a restart.
pub fn save(entries: &Map<String, Value>) -> String {
    serde_json::to_string(&Value::Object(entries.clone())).unwrap_or_default()
}

/// Restore session cache entries; corrupt/absent input yields an empty map.
pub fn load(s: &str) -> Map<String, Value> {
    serde_json::from_str::<Value>(s)
        .ok()
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn roundtrips() {
        let mut m = Map::new();
        m.insert("k1".into(), json!({"a": 1}));
        m.insert("k2".into(), json!([1, 2, 3]));
        let restored = load(&save(&m));
        assert_eq!(restored, m);
    }

    #[test]
    fn corrupt_is_empty() {
        assert!(load("not json").is_empty());
    }
}
```

Add to `tokendog-gate/src/lib.rs`:
```rust
pub mod session;
```

- [ ] **Step 2: Run the tests**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test session`
Expected: 2 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/src/session.rs tokendog-gate/src/lib.rs
git commit -m "feat: gate session persistence (serialize cache across restart)"
```

---

### Task 4: Exact-match Q→A cache (`qa_cache.rs`)

**Files:**
- Create: `tokendog-gate/src/qa_cache.rs`
- Modify: `tokendog-gate/src/lib.rs` (add `pub mod qa_cache;`)

**Interfaces:**
- Produces: `qa_cache::QaCache` with `new()`, `put(&mut self, question: &str, answer: &str)`, `get(&self, question: &str) -> Option<&String>`. Matching is normalized (trim + lowercase) exact match — embedding-based *semantic* matching is a documented follow-up.

- [ ] **Step 1: Write the module WITH its failing test**

`tokendog-gate/src/qa_cache.rs`:
```rust
use std::collections::HashMap;

fn normalize(q: &str) -> String {
    q.trim().to_lowercase()
}

/// Exact/normalized-match Q->A cache. (Embedding-based semantic match is a documented follow-up —
/// it needs a model, which this project does not assume.)
pub struct QaCache {
    map: HashMap<String, String>,
}

impl QaCache {
    pub fn new() -> Self {
        QaCache { map: HashMap::new() }
    }

    pub fn put(&mut self, question: &str, answer: &str) {
        self.map.insert(normalize(question), answer.to_string());
    }

    pub fn get(&self, question: &str) -> Option<&String> {
        self.map.get(&normalize(question))
    }
}

impl Default for QaCache {
    fn default() -> Self { Self::new() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalized_hit() {
        let mut c = QaCache::new();
        c.put("How does auth work?", "It uses JWT.");
        assert_eq!(c.get("  how does AUTH work?  "), Some(&"It uses JWT.".to_string()));
    }

    #[test]
    fn miss_is_none() {
        let c = QaCache::new();
        assert!(c.get("anything").is_none());
    }
}
```

Add to `tokendog-gate/src/lib.rs`:
```rust
pub mod qa_cache;
```

- [ ] **Step 2: Run the tests**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test qa_cache`
Expected: 2 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/src/qa_cache.rs tokendog-gate/src/lib.rs
git commit -m "feat: gate exact-match Q->A cache (semantic deferred)"
```

---

### Task 5: README advanced section + full crate test

**Files:**
- Modify: `tokendog-gate/README.md` (add an "Advanced transforms" section + a "Deferred (follow-up)" section)
- Create: `tokendog-gate/tests/advanced.rs`

**Interfaces:**
- Produces: an integration test exercising CCR + session together, and README docs for the advanced transforms and the honest deferrals.

- [ ] **Step 1: Write the integration test**

`tokendog-gate/tests/advanced.rs`:
```rust
use serde_json::json;
use tokendog_gate::{ccr, session, truncate, qa_cache};

#[test]
fn ccr_roundtrip_and_session_persist() {
    let mut c = ccr::Ccr::new();
    let original = json!({"rows": (0..200).collect::<Vec<i32>>()});
    let (compressed, token) = c.stash(&original, 5, 100);
    assert!(compressed["rows"].as_array().unwrap().len() < 200);
    assert_eq!(c.retrieve(&token), Some(&original));

    let mut entries = serde_json::Map::new();
    entries.insert(token.clone(), original.clone());
    let restored = session::load(&session::save(&entries));
    assert_eq!(restored.get(&token), Some(&original));
}

#[test]
fn truncate_and_qa_cache() {
    let big = (0..500).map(|i| i.to_string()).collect::<Vec<_>>().join("\n");
    let (_, cut) = truncate::head_tail(&big, 5);
    assert!(cut);

    let mut qc = qa_cache::QaCache::new();
    qc.put("Ping?", "Pong");
    assert_eq!(qc.get("ping?"), Some(&"Pong".to_string()));
}
```

- [ ] **Step 2: Update the README**

Append to `tokendog-gate/README.md`:
```markdown

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
```

- [ ] **Step 3: Run the full crate test suite**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test`
Expected: all unit tests (compress/caching/dedup/observe/ccr/truncate/session/qa_cache) + all integration tests pass.

- [ ] **Step 4: Commit**

```bash
git add tokendog-gate/README.md tokendog-gate/tests/advanced.rs
git commit -m "feat: gate advanced integration tests + README (advanced + deferred)"
```

---

## Exit criterion (Slice 7 done)

`cd tokendog-gate && $HOME/.cargo/bin/cargo test` passes all unit + integration tests including CCR, head+tail truncation, session persistence, and the Q→A cache. The README documents the advanced transforms and honestly scopes the network/model-dependent features (SQLite CCR, semantic cache, Ollama, Memory API, live proxy) as follow-ups. No Python suite regressions.

## Self-review notes

- **Spec coverage (spec §8 Phase 5):** CCR (Task 1, item 12) · response truncation heuristics (Task 2, item 14 server) · session persistence (Task 3, item 21) · semantic Q→A cache (Task 4, item 28 — exact-match variant, embedding deferred) · routing/Ollama (item 27), Memory API (item 47), SQLite CCR, live proxy — all explicitly documented as follow-ups (need network/model, not pipeline-testable).
- **Placeholders:** none — every module has complete Rust + tests; deferrals are documented, not fake-stubbed.
- **Determinism:** CCR tokens use a monotonic counter (no time/random), so tests are stable.
- **Compile safety:** each task adds its `pub mod` with its file; `Ccr`/`QaCache` provide `Default` to satisfy clippy-style expectations.
