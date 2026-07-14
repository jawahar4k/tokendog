from tokendog_mcp.batch import run_batch

def test_dedupes_calls():
    calls = []
    def fetch(k):
        calls.append(k)
        return k * 2
    result = run_batch(["a", "b", "a", "b", "b"], fetch)
    assert result == {"a": "aa", "b": "bb"}
    assert calls == ["a", "b"]  # each unique key fetched exactly once

def test_empty():
    assert run_batch([], lambda k: k) == {}
