//! tokendog_gate — token-optimization transforms for the TokenDog gate.
//! Modules are added slice-by-slice; see docs for the (follow-up) proxy wiring.

pub mod compress;
pub mod caching;
pub mod dedup;
pub mod observe;
pub mod ccr;
pub mod truncate;
pub mod session;
pub mod qa_cache;

/// Crush + canonicalize a JSON string; pass non-JSON through unchanged.
pub fn transform(input: &str) -> String {
    match serde_json::from_str::<serde_json::Value>(input) {
        Ok(v) => caching::canonical_string(&compress::crush(&v, 50, 2000)),
        Err(_) => input.to_string(),
    }
}
