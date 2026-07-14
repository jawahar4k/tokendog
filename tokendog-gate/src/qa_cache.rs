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

    #[test]
    fn put_overwrites_existing_answer() {
        // last-write-wins: same normalized key → second put replaces first
        let mut c = QaCache::new();
        c.put("Ping?", "Pong v1");
        c.put("ping?", "Pong v2");
        assert_eq!(c.get("ping?"), Some(&"Pong v2".to_string()));
    }

    #[test]
    fn default_is_new() {
        let mut c = QaCache::default();
        c.put("q", "a");
        assert_eq!(c.get("q"), Some(&"a".to_string()));
    }
}
