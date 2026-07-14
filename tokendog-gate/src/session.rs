use serde_json::{Map, Value};

/// Serialize session cache entries to a string that survives a restart.
pub fn save(entries: &Map<String, Value>) -> String {
    serde_json::to_string(&Value::Object(entries.clone())).expect("serde_json::Value serialization is infallible")
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

    #[test]
    fn array_json_is_empty() {
        // valid JSON but not an object — treated as corrupt input
        assert!(load("[1,2,3]").is_empty());
    }

    #[test]
    fn save_empty_map_roundtrips() {
        let m = Map::new();
        let restored = load(&save(&m));
        assert!(restored.is_empty());
    }
}
