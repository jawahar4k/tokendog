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
fn dedup_removes_duplicate_entries() {
    let (deduped, removed) = dedup::dedup_contents(&["x".into(), "x".into(), "y".into()]);
    assert_eq!(deduped.len(), 2);
    assert_eq!(removed, 1);
}

#[test]
fn parse_usage_extracts_token_counts() {
    let u = observe::parse_usage(r#"{"usage":{"input_tokens":7,"output_tokens":3}}"#).unwrap();
    assert_eq!(u.input_tokens, 7);
}

#[test]
fn cli_passthrough_non_json() {
    assert_eq!(tokendog_gate::transform("not valid json"), "not valid json");
    assert_eq!(tokendog_gate::transform("plain text string"), "plain text string");
    assert_eq!(tokendog_gate::transform("{incomplete"), "{incomplete");
}
