"""Tool-output condenser — deterministic tiers + safety caps."""
from tokendog.condense import condense, deterministic_condense
from tokendog.approx import approx_tokens


def test_grep_output_keeps_first_matches_and_counts_rest():
    text = "\n".join(f"src/f{i}.ts:{i}: match here" for i in range(300))
    digest, method = deterministic_condense(text, "Bash", "grep -rn match src/")
    assert method == "grep-matches"
    assert approx_tokens(digest) < approx_tokens(text)
    assert "more" in digest and "80 matches" in digest


def test_small_grep_is_left_alone():
    text = "\n".join(f"m{i}" for i in range(10))
    digest, method = deterministic_condense(text, "Bash", "grep x .")
    assert digest is None      # under cap → don't touch


def test_test_output_keeps_failures():
    lines = ["PASS test_a"] * 400 + ["FAIL test_b: assertion error", "E   assert 1 == 2"] + ["PASS test_c"] * 50
    text = "\n".join(lines)
    digest, method = deterministic_condense(text, "Bash", "pytest -q")
    assert method == "errors"
    assert "FAIL test_b" in digest and "assert 1 == 2" in digest
    assert approx_tokens(digest) < approx_tokens(text)


def test_generic_large_output_head_tail():
    text = "\n".join(f"line {i}" for i in range(500))
    digest, method = deterministic_condense(text, "Bash", "cat big.log")
    assert method == "head-tail"
    assert "elided" in digest
    assert "line 0" in digest and "line 499" in digest


def test_condense_never_grows_and_reports():
    text = "\n".join(f"line {i}" for i in range(500))
    r = condense(text, tool_name="Bash", command="cat big.log")
    assert r["reduced"] is True
    assert r["digest_tokens"] < r["raw_tokens"]
    assert r["method"] == "head-tail" and r["worker_used"] is False


def test_condense_leaves_small_output_untouched():
    text = "just three\nshort\nlines"
    r = condense(text, tool_name="Bash", command="echo hi")
    assert r["reduced"] is False and r["digest"] == text


def test_worker_not_called_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TOKENDOG_WORKER_KEY", raising=False)
    # prose that deterministic won't confidently reduce (few lines, but long)
    text = "A very long paragraph. " * 400
    r = condense(text, tool_name="WebFetch", command="", use_worker=True)
    # no key → worker skipped; deterministic may not apply → not reduced (safe)
    assert r["worker_used"] is False


def test_digest_carries_a_pointer_to_full_output():
    text = "\n".join(f"line {i}" for i in range(500))
    r = condense(text, tool_name="Bash", command="cat big.log")
    assert "TokenDog" in r["digest"] and "lines total" in r["digest"]
