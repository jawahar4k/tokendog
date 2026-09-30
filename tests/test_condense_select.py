"""The condenser's selection rules, ported from the notes' section 5."""
import pytest

from tokendog.condense import (
    CLIP_CHARS,
    HEAD_LINES,
    MIN_CONDENSE_TOKENS,
    SIGNAL_BUDGET_TOKENS,
    TAIL_LINES,
    condense,
    deterministic_condense,
    select_lines,
)


def _log(n, *, every=None, line="worker processed batch {i} in {ms}ms"):
    out = []
    for i in range(n):
        out.append(line.format(i=i, ms=100 + (i * 37) % 900))
    return out


def _big(lines):
    return "\n".join(lines)


# --- staying out of the way --------------------------------------------------


def test_output_under_the_token_floor_is_left_alone():
    """Lines are the wrong unit: 300 short lines can be 1k tokens. Below the
    floor there is nothing worth a round trip to get back."""
    text = _big([f"ok {i}" for i in range(400)])
    digest, method, _ = deterministic_condense(text, "Bash", "docker logs app")
    assert digest is None and method == ""


def test_the_floor_is_tokens_not_lines():
    assert MIN_CONDENSE_TOKENS == 8_000


@pytest.mark.parametrize("command", [
    "jq . big.json", "git blame src/a.py", "git log -p -- src/", "gh pr diff 123",
    "cat a.log", "sed -n 1,900p f", "git diff", "git show HEAD",
])
def test_content_commands_are_never_condensed(command):
    """Where exact text matters, a digest is wrong however good it is."""
    text = _big(_log(3_000))
    digest, _, _ = deterministic_condense(text, "Bash", command)
    assert digest is None


def test_the_grep_tool_is_measured_but_never_changed():
    """Fleet data decides whether Grep is safe to cut. Until it does, it is not."""
    text = _big([f"src/f{i}.ts:{i}: match here and more text" for i in range(4_000)])
    r = condense(text, tool_name="Grep", command="")
    assert r["reduced"] is False
    assert r["digest"] == text
    assert r.get("would_reduce") is True     # measured: we know it could


# --- what is kept ------------------------------------------------------------


def test_the_head_and_tail_are_kept_verbatim():
    lines = _log(3_000)
    kept = select_lines(lines)
    assert kept[:HEAD_LINES] == list(range(HEAD_LINES))
    assert kept[-TAIL_LINES:] == list(range(3_000 - TAIL_LINES, 3_000))


def test_every_problem_line_is_kept_with_three_lines_after():
    """The line after an error is usually the one that says why."""
    lines = _log(3_000)
    lines[1500] = "ERROR: connection refused to postgres://db:5432"
    lines[1501] = "  caused by: dial tcp 10.0.0.5:5432: i/o timeout"
    kept = set(select_lines(lines))
    assert {1500, 1501, 1502, 1503} <= kept
    assert 1504 not in kept


@pytest.mark.parametrize("word", ["error", "FAIL", "Warning", "Exception", "Traceback",
                                  "permission denied", "timed out", "not found", "✗"])
def test_problem_words_are_recognised(word):
    lines = _log(3_000)
    lines[1200] = f"step 1200: {word} while processing"
    assert 1200 in set(select_lines(lines))


def test_a_stack_trace_keeps_its_frames_up_to_a_limit():
    lines = _log(3_000)
    lines[1000] = "Traceback (most recent call last):"
    for k in range(1, 40):
        lines[1000 + k] = f'  File "app/mod{k}.py", line {k}, in fn{k}'
    kept = set(select_lines(lines))
    frames = [i for i in range(1001, 1040) if i in kept]
    assert 1000 in kept
    assert 3 < len(frames) <= 20 + 3          # up to 20 frames, plus the 3-after window


def test_the_signal_budget_caps_what_problem_lines_can_pull_in():
    """A log where every line says `error` is not a log with errors in it; it
    is a log. The budget stops the digest turning back into the input."""
    lines = [f"error: retry {i} of many, backing off for {i % 50}ms" for i in range(6_000)]
    r = condense(_big(lines), tool_name="Bash", command="docker logs app")
    assert r["reduced"]
    assert r["digest_tokens"] < SIGNAL_BUDGET_TOKENS + 3_000


# --- making what is kept smaller ---------------------------------------------


def test_repeats_that_differ_only_in_numbers_collapse():
    """`batch 1204 in 331ms` and `batch 1205 in 402ms` are one line, many times."""
    lines = _log(3_000)
    lines[1500] = "ERROR: first"
    for k in range(1501, 1504):
        lines[k] = f"retrying request {k} after {k * 3}ms"
    r = condense(_big(lines), tool_name="Bash", command="docker logs app")
    assert "×" in r["digest"]


def test_head_and_tail_are_never_collapsed():
    """The first and last lines are where a reader looks first; they stay exact."""
    lines = _log(3_000)
    r = condense(_big(lines), tool_name="Bash", command="docker logs app")
    for i in (0, 1, 2, 2_997, 2_998, 2_999):
        assert lines[i] in r["digest"]


def test_hashes_count_as_noise_when_collapsing():
    lines = _log(3_000)
    lines[1500] = "WARN: cache miss"
    for k in range(1501, 1504):
        lines[k] = f"fetched object {k:040x} ok"
    r = condense(_big(lines), tool_name="Bash", command="docker logs app")
    assert "×" in r["digest"]


def test_a_very_long_line_is_clipped_in_the_digest():
    lines = _log(3_000)
    lines[1500] = "ERROR: " + "x" * 20_000
    r = condense(_big(lines), tool_name="Bash", command="docker logs app")
    longest = max(len(ln) for ln in r["digest"].splitlines())
    assert longest <= CLIP_CHARS + 60


def test_kept_ranges_still_describe_the_digest():
    """The spill pointer is built from these; they must match what was kept."""
    lines = _log(3_000)
    lines[1500] = "ERROR: boom"
    r = condense(_big(lines), tool_name="Bash", command="docker logs app")
    covered = {n for a, b in r["kept_ranges"] for n in range(a, b + 1)}
    assert 1 in covered and 3_000 in covered and 1_501 in covered   # 1-based
    assert len(covered) < 3_000


# --- every tunable has an override (notes §7) --------------------------------


def test_the_floor_can_be_lowered_for_a_workload_with_smaller_outputs(monkeypatch):
    """8k is ORBIT's measured choice for its fleet. On a workload whose p90
    command output is 5k, the condenser has nothing to do at that floor; the
    reader needs a way to try a lower one without editing code."""
    import importlib
    import tokendog.condense as c
    monkeypatch.setenv("TOKENDOG_CONDENSE_MIN_TOKENS", "1000")
    c = importlib.reload(c)
    try:
        text = "\n".join(f"[{i:05d}] worker processed batch {i}" for i in range(600))
        assert c.deterministic_condense(text, "Bash", "docker logs app")[1] == "select"
    finally:
        monkeypatch.delenv("TOKENDOG_CONDENSE_MIN_TOKENS")
        importlib.reload(c)


@pytest.mark.parametrize("raw,expected", [("nope", 8_000), ("-5", 8_000), ("0", 0), ("2500", 2_500)])
def test_env_num_treats_bad_values_as_default_but_zero_as_valid(monkeypatch, raw, expected):
    from tokendog.condense import _env_int
    monkeypatch.setenv("TOKENDOG_X", raw)
    assert _env_int("TOKENDOG_X", 8_000) == expected
