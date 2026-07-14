use serde_json::{json, Value};

const TRUNCATION_SUFFIX: &str = "...[tokendog: truncated]";

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
            let suffix_chars = TRUNCATION_SUFFIX.chars().count();
            let head_len = max_string.saturating_sub(suffix_chars);
            let head: String = s.chars().take(head_len).collect();
            Value::String(format!("{}{}", head, TRUNCATION_SUFFIX))
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
        let result = c["blob"].as_str().unwrap();
        assert!(result.contains("truncated"));
        assert_eq!(result.chars().count(), 50);
    }

    #[test]
    fn truncates_unicode_without_panic() {
        let v = json!({"blob": "emoji: 🎉".repeat(100)});
        let c = crush(&v, 10, 50);
        let result = c["blob"].as_str().unwrap();
        assert!(result.contains("truncated"));
        assert_eq!(result.chars().count(), 50);
    }

    #[test]
    fn leaves_small_values() {
        let v = json!({"a": [1, 2], "b": "hi"});
        assert_eq!(crush(&v, 10, 50), v);
    }
}
