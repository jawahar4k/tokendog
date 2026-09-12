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
    digest, method = deterministic_condense(text, "Bash", "docker logs app")
    assert method == "head-tail"
    assert "elided" in digest
    assert "line 0" in digest and "line 499" in digest


def test_condense_never_grows_and_reports():
    text = "\n".join(f"line {i}" for i in range(500))
    r = condense(text, tool_name="Bash", command="npm install")
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
    r = condense(text, tool_name="Bash", command="npm install")
    assert "TokenDog" in r["digest"] and "lines total" in r["digest"]


# --- a file the model asked to read is never cut in the middle -------------
#
# Replay over real transcripts showed 91% of head-tail's projected saving came
# from `Read` and `cat -n path/to/file.ts`: the digest dropped the middle of a
# source file the model had deliberately opened. Head and tail carry the story
# of a log or a build; they carry nothing of a file. So head-tail is for command
# output only, and a whole-file read is left alone at any size.

import pytest


@pytest.mark.parametrize("tool,command", [
    ("Read", ""),
    ("Bash", "cat -n packages/workers/src/reconcile/sweep.ts"),
    ("Bash", "cat .glitch/pipeline.md"),
    ("Bash", "sed -n 1,400p src/app.py"),
    ("Bash", "head -300 big.log"),
    ("Bash", "bat README.md"),
    ("Bash", "  cat file.txt | sed 's/a/b/'"),
    ("Bash", "cd /x/y && cat .glitch/pipeline.md"),
    ("Bash", "for f in a.ts b.ts; do echo == $f; cat $f; done"),
    ("Bash", "sudo cat /var/log/x.log"),
])
def test_a_whole_file_read_is_never_head_tailed(tool, command):
    text = "\n".join(f"line {i}: something unique" for i in range(600))
    digest, method = deterministic_condense(text, tool, command)
    assert digest is None and method == ""


@pytest.mark.parametrize("command", [
    "docker logs app", "npm install", "cargo build", "find . -name '*.py'",
    "git log --stat", "curl -s https://example.test/x",
    "pytest -q 2>&1 | tail -300",           # a piped tail is the model capping, not reading
    "git status --porcelain; ls -R | head -400",
])
def test_command_output_still_gets_head_tail(command):
    text = "\n".join(f"line {i}" for i in range(600))
    _, method = deterministic_condense(text, "Bash", command)
    assert method == "head-tail"


def test_a_file_read_mixed_with_a_grep_is_left_alone():
    """`cat f; grep …` must not hit the grep tier: it would keep the first 80
    lines — the file's head — and drop the actual matches."""
    text = "\n".join(f"src/f{i}.ts:{i}: match" for i in range(300))
    digest, method = deterministic_condense(text, "Bash", 'cat src/x.ts; echo ==; grep -n "match" src/')
    assert digest is None and method == ""


def test_a_pure_grep_still_condenses_as_matches():
    text = "\n".join(f"src/f{i}.ts:{i}: match" for i in range(300))
    _, method = deterministic_condense(text, "Bash", "grep -rn match src/")
    assert method == "grep-matches"


@pytest.mark.parametrize("command", ["git diff HEAD~1 -- src/", "git show abc123", "diff -u a b"])
def test_a_diff_is_read_for_its_hunks_and_never_cut(command):
    text = "\n".join(f"+line {i}" for i in range(600))
    digest, method = deterministic_condense(text, "Bash", command)
    assert digest is None and method == ""
