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
        // crush(max_array=10) keeps 10 items + 1 omission marker = 11 total
        assert_eq!(compressed["items"].as_array().unwrap().len(), 11);
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

    #[test]
    fn token_format_is_deterministic() {
        // Pins the counter-based format; a refactor to uuid/SystemTime would break this.
        let mut ccr = Ccr::new();
        let (_, t1) = ccr.stash(&json!(1), 10, 10);
        let (_, t2) = ccr.stash(&json!(2), 10, 10);
        assert_eq!(t1, "tokendog-ccr-1");
        assert_eq!(t2, "tokendog-ccr-2");
    }

    #[test]
    fn default_is_new() {
        let mut ccr = Ccr::default();
        let (_, t) = ccr.stash(&json!("x"), 10, 10);
        assert_eq!(t, "tokendog-ccr-1");
    }
}
