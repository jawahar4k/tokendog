from __future__ import annotations

def run_batch(keys, fetch_one) -> dict:
    """Call fetch_one once per UNIQUE key (order-preserving); return {key: result}."""
    results = {}
    for key in dict.fromkeys(keys):
        results[key] = fetch_one(key)
    return results
