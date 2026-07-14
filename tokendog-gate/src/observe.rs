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
    if u.get("input_tokens").is_none() && u.get("output_tokens").is_none() {
        return None;
    }
    Some(Usage {
        input_tokens: field(u, "input_tokens"),
        output_tokens: field(u, "output_tokens"),
        cache_read: field(u, "cache_read_input_tokens"),
        cache_creation: field(u, "cache_creation_input_tokens"),
    })
}

/// Server-side hard budget check.
pub fn over_budget(spent_usd: f64, limit_usd: Option<f64>) -> bool {
    if !spent_usd.is_finite() { return true; }
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
    fn empty_usage_object_is_none() {
        assert!(parse_usage(r#"{"usage":{}}"#).is_none());
    }

    #[test]
    fn partial_usage_with_one_primary_field() {
        let u = parse_usage(r#"{"usage":{"input_tokens":5}}"#).unwrap();
        assert_eq!(u.input_tokens, 5);
        assert_eq!(u.output_tokens, 0);
    }

    #[test]
    fn budget_logic() {
        assert!(over_budget(10.0, Some(5.0)));
        assert!(!over_budget(1.0, Some(5.0)));
        assert!(!over_budget(1000.0, None));
    }

    #[test]
    fn over_budget_nan_is_over() {
        assert!(over_budget(f64::NAN, Some(1.0)));
        assert!(over_budget(f64::INFINITY, Some(1.0)));
        assert!(over_budget(f64::NEG_INFINITY, Some(1.0)));
    }

    #[test]
    fn over_budget_boundary() {
        assert!(over_budget(5.0, Some(5.0)));
        assert!(!over_budget(4.999, Some(5.0)));
    }
}
