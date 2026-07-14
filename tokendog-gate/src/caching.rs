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
    serde_json::to_string(&canonicalize(value))
        .expect("serde_json::Value serialization is infallible")
}

/// Pooled cache key: N users on a team share the prefix cache within the TTL window.
/// Colons in components are escaped as %3A to prevent cross-tenant collisions.
pub fn cache_key(team: &str, model: &str) -> String {
    let team = team.replace(':', "%3A");
    let model = model.replace(':', "%3A");
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

    #[test]
    fn cache_key_no_cross_tenant_collision() {
        assert_ne!(cache_key("a:b", "c"), cache_key("a", "b:c"));
    }
}
