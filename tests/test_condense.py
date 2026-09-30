"""Tool-output condenser — one selection rule, safety caps, and what it never touches."""
import pytest

from tokendog.approx import approx_tokens
from tokendog.condense import MIN_CONDENSE_TOKENS, condense, deterministic_condense


def _biglog(n=3_000):
    """Clears the 8k-token floor, which lines alone do not: 600 short lines is ~2k tokens."""
    return "\n".join(f"[{i:05d}] worker processed batch {i} in {100 + (i * 37) % 900}ms"
                     for i in range(n))


def _bigfile(n=3_000):
    """A file-shaped payload well over the floor, so a guard test fails on the
    guard and not on the size check."""
    text = "\n".join(f"line {i}: something unique about this particular line" for i in range(n))
    assert approx_tokens(text) >= MIN_CONDENSE_TOKENS, "fixture must clear the floor"
    return text


def test_grep_in_bash_is_command_output_and_gets_the_same_rule():
    """One selection rule for all command output; a grep tier no longer exists."""
    text = "\n".join(f"src/f{i}.ts:{i}: match here with some context around it" for i in range(3_000))
    digest, method, _kept = deterministic_condense(text, "Bash", "grep -rn match src/")
    assert method == "select"
    assert approx_tokens(digest) < approx_tokens(text)


def test_small_grep_is_left_alone():
    text = "\n".join(f"m{i}" for i in range(10))
    digest, method, _kept = deterministic_condense(text, "Bash", "grep x .")
    assert digest is None      # under the floor → don't touch


def test_test_output_keeps_failures():
    lines = ["PASS test_a ok with a reasonably long description line"] * 3_000
    lines[1500] = "FAIL test_b: assertion error"
    lines[1501] = "E   assert 1 == 2"
    text = "\n".join(lines)
    digest, method, _kept = deterministic_condense(text, "Bash", "pytest -q")
    assert method == "select"
    assert "FAIL test_b" in digest and "assert 1 == 2" in digest
    assert approx_tokens(digest) < approx_tokens(text)


def test_generic_large_output_keeps_head_and_tail():
    digest, method, _kept = deterministic_condense(_biglog(), "Bash", "docker logs app")
    assert method == "select"
    assert "not shown" in digest
    assert "[00000]" in digest and "[02999]" in digest


def test_condense_never_grows_and_reports():
    r = condense(_biglog(), tool_name="Bash", command="npm install")
    assert r["reduced"] is True
    assert r["digest_tokens"] < r["raw_tokens"]
    assert r["method"] == "select" and r["worker_used"] is False


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
    r = condense(_biglog(), tool_name="Bash", command="npm install")
    assert "TokenDog" in r["digest"] and "lines total" in r["digest"]


# --- a file the model asked to read is never cut in the middle -------------
#
# Replay over real transcripts showed 91% of the old head-tail tier's projected
# saving came from `Read` and `cat -n path/to/file.ts`: the digest dropped the
# middle of a source file the model had deliberately opened. So a whole-file
# read is left alone at any size. The fixtures here are deliberately over the
# token floor: a small fixture would pass because it is small, and the guard
# could be deleted without a single test noticing.


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
def test_a_whole_file_read_is_never_condensed(tool, command):
    digest, method, _kept = deterministic_condense(_bigfile(), tool, command)
    assert digest is None and method == ""


@pytest.mark.parametrize("command", [
    "docker logs app", "npm install", "cargo build", "find . -name '*.py'",
    "git log --stat", "curl -s https://example.test/x",
    "pytest -q 2>&1 | tail -300",           # a piped tail is the model capping, not reading
    "git status --porcelain; ls -R | head -400",
])
def test_command_output_is_condensed(command):
    _, method, _kept = deterministic_condense(_biglog(), "Bash", command)
    assert method == "select"


def test_a_file_read_mixed_with_a_grep_is_left_alone():
    """`cat f; grep …` is still the file; cutting it would drop what was read."""
    text = "\n".join(f"src/f{i}.ts:{i}: match here with some context around it" for i in range(3_000))
    digest, method, _kept = deterministic_condense(
        text, "Bash", 'cat src/x.ts; echo ==; grep -n "match" src/')
    assert digest is None and method == ""


@pytest.mark.parametrize("command", ["git diff HEAD~1 -- src/", "git show abc123", "diff -u a b"])
def test_a_diff_is_read_for_its_hunks_and_never_cut(command):
    text = "\n".join(f"+line {i}: a changed line with enough text to be real" for i in range(3_000))
    assert approx_tokens(text) >= MIN_CONDENSE_TOKENS
    digest, method, _kept = deterministic_condense(text, "Bash", command)
    assert digest is None and method == ""


# --- kept ranges: what makes the cut reversible ------------------------------


def test_every_digest_reports_which_lines_it_kept():
    """Without this the spill file is just a file: the pointer cannot say which
    ranges are missing, and the model has nothing to ask for."""
    r = condense(_biglog(), tool_name="Bash", command="docker logs app")
    assert r["reduced"]
    assert r["kept_ranges"], "a reduced digest must say what it kept"
    for start, end in r["kept_ranges"]:
        assert 1 <= start <= end <= 3_000


def test_the_kept_ranges_cover_the_head_and_the_tail():
    r = condense(_biglog(), tool_name="Bash", command="docker logs app")
    covered = {n for start, end in r["kept_ranges"] for n in range(start, end + 1)}
    assert 1 in covered and 3_000 in covered
    assert len(covered) < 3_000            # something really was left out


def test_an_untouched_output_keeps_everything():
    r = condense("a\nb\nc", tool_name="Bash", command="echo hi")
    assert not r["reduced"]
    assert r["kept_ranges"] == [(1, 3)]


def test_the_head_block_is_one_range():
    r = condense(_biglog(), tool_name="Bash", command="docker logs app")
    assert r["kept_ranges"][0] == (1, 30)


def test_the_kept_ranges_include_a_failure_and_what_follows_it():
    lines = ["PASS a with a reasonably long description line"] * 3_000
    lines[1500] = "FAIL b: assertion error"
    r = condense("\n".join(lines), tool_name="Bash", command="pytest -q")
    covered = {n for start, end in r["kept_ranges"] for n in range(start, end + 1)}
    assert {1501, 1502, 1503, 1504} <= covered        # 1-based: the FAIL and 3 after
