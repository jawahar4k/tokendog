# TokenDog Slice 6 — Gate Core (Rust) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the transport gate's core token-optimization transforms as a unit-tested Rust library (`tokendog_gate`) plus a stdin→stdout CLI that applies them — JSON compression (SmartCrusher-inspired), canonical ordering + cache-key pooling, context dedup, `usage.*` parsing, and server-side budget checks.

**Architecture:** A standalone Cargo project at `tokendog-gate/`. Each transform is a pure, `cargo test`-tested module. A thin `main.rs` CLI reads a JSON request body on stdin, applies crush + canonicalize, and writes it to stdout — demonstrating what the proxy does to a request before forwarding. **The live HTTPS proxy (terminate TLS, forward to api.anthropic.com, stream responses) is an explicit follow-up**, documented in the gate README — it needs a live API to test meaningfully and the user will validate it. This slice delivers the tested algorithms that the proxy will wire together.

**Tech Stack:** Rust (stable 1.97), `serde_json`. Cargo at `$HOME/.cargo/bin/cargo`.

## Global Constraints

- Prefix `tokendog`; crate `tokendog_gate` / binary `tokendog-gate`; Apache-2.0; Rust edition 2021.
- Only dependency: `serde_json`. No async/proxy crates in this slice (the network proxy is the documented follow-up).
- Cargo is not on PATH in the pipeline shell — invoke it as **`$HOME/.cargo/bin/cargo`**.
- All commands run from `tokendog-gate/`, e.g. `cd tokendog-gate && $HOME/.cargo/bin/cargo test`.
- `src/lib.rs` declares a module only once its file exists — each task adds its `pub mod` line together with the module, so every task compiles.
- String truncation must be char-boundary-safe (use `chars()`, never byte slicing) to avoid panics.
- This slice does NOT touch the Python suite; keep it self-contained under `tokendog-gate/`.
- TDD via Rust `#[cfg(test)]` modules; commit after each green task.

---

### Task 1: Cargo scaffold

**Files:**
- Create: `tokendog-gate/Cargo.toml`
- Create: `tokendog-gate/src/lib.rs`
- Create: `tokendog-gate/src/main.rs`
- Create: `tokendog-gate/.gitignore`

**Interfaces:**
- Produces: a compiling crate with a lib + binary target; `cargo build` and `cargo test` succeed (no tests yet).

- [ ] **Step 1: Create the project files**

`tokendog-gate/Cargo.toml`:
```toml
[package]
name = "tokendog-gate"
version = "0.1.0"
edition = "2021"
license = "Apache-2.0"
description = "TokenDog transport gate — token-optimization transforms."

[dependencies]
serde_json = "1"

[lib]
name = "tokendog_gate"
path = "src/lib.rs"

[[bin]]
name = "tokendog-gate"
path = "src/main.rs"
```

`tokendog-gate/src/lib.rs`:
```rust
//! tokendog_gate — token-optimization transforms for the TokenDog gate.
//! Modules are added slice-by-slice; see docs for the (follow-up) proxy wiring.
```

`tokendog-gate/src/main.rs`:
```rust
fn main() {
    // CLI is implemented in Task 6; scaffold builds cleanly for now.
}
```

`tokendog-gate/.gitignore`:
```
/target
```

- [ ] **Step 2: Verify it builds and tests**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo build && $HOME/.cargo/bin/cargo test`
Expected: build succeeds (downloads `serde_json` on first run); `cargo test` reports `0 tests`.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/Cargo.toml tokendog-gate/src/lib.rs tokendog-gate/src/main.rs tokendog-gate/.gitignore
git commit -m "feat: tokendog-gate cargo scaffold (lib + bin, serde_json)"
```

---

### Task 2: JSON compression (`compress.rs`)

**Files:**
- Create: `tokendog-gate/src/compress.rs`
- Modify: `tokendog-gate/src/lib.rs` (add `pub mod compress;`)

**Interfaces:**
- Produces: `compress::crush(value: &serde_json::Value, max_array: usize, max_string: usize) -> serde_json::Value` — caps arrays to `max_array` items (+ an omitted-count marker element) and truncates strings longer than `max_string` chars, recursing through objects/arrays.

- [ ] **Step 1: Write the module WITH its failing test**

`tokendog-gate/src/compress.rs`:
```rust
use serde_json::{json, Value};

/// SmartCrusher-inspired: cap long arrays and truncate long strings, recursively.
pub fn crush(value: &Value, max_array: usize, max_string: usize) -> Value {
    match value {
        Value::Array(a) => {
            let mut out: Vec<Value> =
                a.iter().take(max_array).map(|v| crush(v, max_array, max_string)).collect();
            if a.len() > max_array {
                out.push(json!(format!("...[tokendog: {} more items omitted]", a.len() - max_array)));
            }
            Value::Array(out)
        }
        Value::Object(o) => {
            let mut m = serde_json::Map::new();
            for (k, v) in o {
                m.insert(k.clone(), crush(v, max_array, max_string));
            }
            Value::Object(m)
        }
        Value::String(s) if s.chars().count() > max_string => {
            let head: String = s.chars().take(max_string).collect();
            Value::String(format!("{}...[tokendog: truncated]", head))
        }
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn caps_long_arrays() {
        let v = json!({"items": (0..100).collect::<Vec<i32>>()});
        let c = crush(&v, 10, 1000);
        let arr = c["items"].as_array().unwrap();
        assert_eq!(arr.len(), 11); // 10 kept + 1 marker
        assert!(arr[10].as_str().unwrap().contains("omitted"));
    }

    #[test]
    fn truncates_long_strings() {
        let v = json!({"blob": "x".repeat(500)});
        let c = crush(&v, 10, 50);
        assert!(c["blob"].as_str().unwrap().contains("truncated"));
        assert!(c["blob"].as_str().unwrap().len() < 500);
    }

    #[test]
    fn leaves_small_values() {
        let v = json!({"a": [1, 2], "b": "hi"});
        assert_eq!(crush(&v, 10, 50), v);
    }
}
```

Add to `tokendog-gate/src/lib.rs`:
```rust
pub mod compress;
```

- [ ] **Step 2: Run the tests (verify pass)**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test compress`
Expected: 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/src/compress.rs tokendog-gate/src/lib.rs
git commit -m "feat: gate JSON compression (crush arrays + strings)"
```

---

### Task 3: Canonical ordering + cache-key pooling (`caching.rs`)

**Files:**
- Create: `tokendog-gate/src/caching.rs`
- Modify: `tokendog-gate/src/lib.rs` (add `pub mod caching;`)

**Interfaces:**
- Produces: `caching::canonicalize(value: &Value) -> Value` (recursively sorts object keys); `caching::canonical_string(value: &Value) -> String` (serialized canonical form); `caching::cache_key(team: &str, model: &str) -> String` (pooled `prompt_cache_key` so a team shares cache).

- [ ] **Step 1: Write the module WITH its failing test**

`tokendog-gate/src/caching.rs`:
```rust
use serde_json::Value;

/// Recursively rebuild with object keys sorted, for a byte-identical canonical form.
pub fn canonicalize(value: &Value) -> Value {
    match value {
        Value::Object(o) => {
            let mut keys: Vec<&String> = o.keys().collect();
            keys.sort();
            let mut m = serde_json::Map::new();
            for k in keys {
                m.insert(k.clone(), canonicalize(&o[k]));
            }
            Value::Object(m)
        }
        Value::Array(a) => Value::Array(a.iter().map(canonicalize).collect()),
        other => other.clone(),
    }
}

pub fn canonical_string(value: &Value) -> String {
    serde_json::to_string(&canonicalize(value)).unwrap_or_default()
}

/// Pooled cache key: N users on a team share the prefix cache within the TTL window.
pub fn cache_key(team: &str, model: &str) -> String {
    format!("tokendog:{}:{}", team, model)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn key_order_does_not_change_canonical_form() {
        let a = json!({"b": 1, "a": 2, "c": {"y": 1, "x": 2}});
        let b = json!({"a": 2, "c": {"x": 2, "y": 1}, "b": 1});
        assert_eq!(canonical_string(&a), canonical_string(&b));
        assert!(canonical_string(&a).starts_with("{\"a\""));
    }

    #[test]
    fn cache_key_is_stable_and_pooled() {
        assert_eq!(cache_key("payments", "sonnet"), "tokendog:payments:sonnet");
        assert_eq!(cache_key("payments", "sonnet"), cache_key("payments", "sonnet"));
    }
}
```

Add to `tokendog-gate/src/lib.rs`:
```rust
pub mod caching;
```

- [ ] **Step 2: Run the tests**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test caching`
Expected: 2 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/src/caching.rs tokendog-gate/src/lib.rs
git commit -m "feat: gate canonical ordering + pooled cache key"
```

---

### Task 4: Context dedup (`dedup.rs`)

**Files:**
- Create: `tokendog-gate/src/dedup.rs`
- Modify: `tokendog-gate/src/lib.rs` (add `pub mod dedup;`)

**Interfaces:**
- Produces: `dedup::dedup_contents(contents: &[String]) -> (Vec<String>, usize)` — keeps first occurrence of each unique string (order-preserving), returns `(deduped, removed_count)`.

- [ ] **Step 1: Write the module WITH its failing test**

`tokendog-gate/src/dedup.rs`:
```rust
use std::collections::HashSet;

/// Drop exact-duplicate message contents (keep first occurrence).
pub fn dedup_contents(contents: &[String]) -> (Vec<String>, usize) {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for c in contents {
        if seen.insert(c.as_str().to_owned()) {
            out.push(c.clone());
        }
    }
    let removed = contents.len() - out.len();
    (out, removed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn removes_exact_duplicates_preserving_order() {
        let input = vec!["a".to_string(), "b".to_string(), "a".to_string(),
                         "c".to_string(), "b".to_string()];
        let (out, removed) = dedup_contents(&input);
        assert_eq!(out, vec!["a", "b", "c"]);
        assert_eq!(removed, 2);
    }

    #[test]
    fn empty_is_empty() {
        let (out, removed) = dedup_contents(&[]);
        assert!(out.is_empty() && removed == 0);
    }
}
```

Add to `tokendog-gate/src/lib.rs`:
```rust
pub mod dedup;
```

- [ ] **Step 2: Run the tests**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test dedup`
Expected: 2 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/src/dedup.rs tokendog-gate/src/lib.rs
git commit -m "feat: gate context dedup (drop repeated content)"
```

---

### Task 5: Usage parsing + budget check (`observe.rs`)

**Files:**
- Create: `tokendog-gate/src/observe.rs`
- Modify: `tokendog-gate/src/lib.rs` (add `pub mod observe;`)

**Interfaces:**
- Produces: `observe::Usage { input_tokens, output_tokens, cache_read, cache_creation }` (all `u64`, derives `Debug, PartialEq`); `observe::parse_usage(response_body: &str) -> Option<Usage>` (reads the `usage` object from an Anthropic response JSON); `observe::over_budget(spent_usd: f64, limit_usd: Option<f64>) -> bool`.

- [ ] **Step 1: Write the module WITH its failing test**

`tokendog-gate/src/observe.rs`:
```rust
use serde_json::Value;

#[derive(Debug, PartialEq)]
pub struct Usage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_read: u64,
    pub cache_creation: u64,
}

fn field(u: &Value, name: &str) -> u64 {
    u.get(name).and_then(|x| x.as_u64()).unwrap_or(0)
}

/// Extract authoritative token usage from an Anthropic API response body.
pub fn parse_usage(response_body: &str) -> Option<Usage> {
    let v: Value = serde_json::from_str(response_body).ok()?;
    let u = v.get("usage")?;
    Some(Usage {
        input_tokens: field(u, "input_tokens"),
        output_tokens: field(u, "output_tokens"),
        cache_read: field(u, "cache_read_input_tokens"),
        cache_creation: field(u, "cache_creation_input_tokens"),
    })
}

/// Server-side hard budget check.
pub fn over_budget(spent_usd: f64, limit_usd: Option<f64>) -> bool {
    match limit_usd {
        Some(limit) => spent_usd >= limit,
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_usage_fields() {
        let body = r#"{"id":"msg_1","usage":{"input_tokens":123,"output_tokens":45,
                       "cache_read_input_tokens":10,"cache_creation_input_tokens":5}}"#;
        let u = parse_usage(body).unwrap();
        assert_eq!(u, Usage { input_tokens: 123, output_tokens: 45, cache_read: 10, cache_creation: 5 });
    }

    #[test]
    fn missing_usage_is_none() {
        assert!(parse_usage(r#"{"id":"x"}"#).is_none());
        assert!(parse_usage("not json").is_none());
    }

    #[test]
    fn budget_logic() {
        assert!(over_budget(10.0, Some(5.0)));
        assert!(!over_budget(1.0, Some(5.0)));
        assert!(!over_budget(1000.0, None));
    }
}
```

Add to `tokendog-gate/src/lib.rs`:
```rust
pub mod observe;
```

- [ ] **Step 2: Run the tests**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test observe`
Expected: 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tokendog-gate/src/observe.rs tokendog-gate/src/lib.rs
git commit -m "feat: gate usage parsing + server-side budget check"
```

---

### Task 6: CLI wiring + integration test + gate README

**Files:**
- Modify: `tokendog-gate/src/main.rs`
- Create: `tokendog-gate/tests/integration.rs`
- Create: `tokendog-gate/README.md`

**Interfaces:**
- Produces: a CLI that reads a JSON request body from stdin, applies `crush` then `canonical_string`, and writes the result to stdout (non-JSON input passes through unchanged); an integration test exercising the full transform chain; a README documenting the gate and the follow-up proxy wiring.

- [ ] **Step 1: Write the CLI + integration test**

`tokendog-gate/src/main.rs`:
```rust
use std::io::{self, Read, Write};
use serde_json::Value;
use tokendog_gate::{caching, compress};

fn transform(input: &str) -> String {
    match serde_json::from_str::<Value>(input) {
        Ok(v) => caching::canonical_string(&compress::crush(&v, 50, 2000)),
        Err(_) => input.to_string(),
    }
}

fn main() {
    let mut input = String::new();
    if io::stdin().read_to_string(&mut input).is_err() {
        return;
    }
    let out = transform(&input);
    let _ = io::stdout().write_all(out.as_bytes());
}
```

`tokendog-gate/tests/integration.rs`:
```rust
use serde_json::json;
use tokendog_gate::{caching, compress, dedup, observe};

#[test]
fn crush_then_canonicalize_chain() {
    let big = json!({"b": (0..100).collect::<Vec<i32>>(), "a": "small"});
    let crushed = compress::crush(&big, 10, 100);
    let s = caching::canonical_string(&crushed);
    assert!(s.starts_with("{\"a\"")); // sorted keys
    assert!(s.contains("omitted"));   // array capped
}

#[test]
fn dedup_and_usage_together() {
    let (deduped, removed) = dedup::dedup_contents(&["x".into(), "x".into(), "y".into()]);
    assert_eq!(deduped.len(), 2);
    assert_eq!(removed, 1);
    let u = observe::parse_usage(r#"{"usage":{"input_tokens":7,"output_tokens":3}}"#).unwrap();
    assert_eq!(u.input_tokens, 7);
}
```

`tokendog-gate/README.md`:
```markdown
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
```

- [ ] **Step 2: Run the full crate test suite**

Run: `cd tokendog-gate && $HOME/.cargo/bin/cargo test`
Expected: all unit tests (compress/caching/dedup/observe) + both integration tests pass.

- [ ] **Step 3: Verify the CLI end-to-end**

Run: `cd tokendog-gate && echo '{"z":1,"a":[0,1,2,3,4,5]}' | $HOME/.cargo/bin/cargo run --quiet`
Expected: prints canonical JSON with `a` before `z` (keys sorted); the array is small so it's unchanged.

- [ ] **Step 4: Commit**

```bash
git add tokendog-gate/src/main.rs tokendog-gate/tests/integration.rs tokendog-gate/README.md
git commit -m "feat: gate CLI (crush+canonicalize) + integration tests + README"
```

---

## Exit criterion (Slice 6 done)

`cd tokendog-gate && $HOME/.cargo/bin/cargo test` passes all unit + integration tests; the CLI transforms a JSON request body (crush + canonicalize) end to end. The gate README documents the transforms and scopes the live proxy as the manual-validation follow-up. No Python suite regressions.

## Self-review notes

- **Spec coverage (spec §8 Phase 4):** JSON compression (Task 2, item 11) · cache-key pooling (Task 3, item 20) · prefix stability / deterministic ordering (Task 3, items 17–18) · context dedup (Task 4, item 13) · usage capture (Task 5, item 33) · server-side budgets (Task 5, item 36). The live proxy skeleton (terminate/forward) is explicitly scoped as a documented follow-up — honest, since it can't be pipeline-tested without a live API.
- **Placeholders:** none — every module has complete Rust + tests.
- **Compile safety:** each task adds its `pub mod` line with its file, so the crate compiles at every step; string truncation uses `chars()` (no byte-boundary panics); `Usage` derives `PartialEq` for the assert.
- **Isolation:** self-contained under `tokendog-gate/`; `/target` git-ignored; does not affect the Python package or its tests.
