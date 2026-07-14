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

    // A fresh Ccr has no knowledge of tokens from the previous instance
    let fresh = ccr::Ccr::new();
    assert!(fresh.retrieve(&token).is_none(), "in-memory and session are separate stores");
}

#[test]
fn multi_stash_retrieve_correctness() {
    let mut c = ccr::Ccr::new();
    let v1 = json!({"kind": "first", "data": (0..50).collect::<Vec<i32>>()});
    let v2 = json!({"kind": "second", "data": (100..200).collect::<Vec<i32>>()});
    let (_, t1) = c.stash(&v1, 5, 100);
    let (_, t2) = c.stash(&v2, 5, 100);
    assert_ne!(t1, t2);
    assert_eq!(c.retrieve(&t1), Some(&v1));
    assert_eq!(c.retrieve(&t2), Some(&v2));
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
