"""The read guard: stop a large whole-file read before it enters the window."""
import pytest

from tokendog.readguard import (
    CHARS_PER_TOKEN,
    DEFAULT_RATIO,
    MIN_TOKENS,
    SKIP_EXT,
    Decision,
    assess,
    block_pays,
    estimate_tokens,
    is_whole_file_read,
)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# --- the cost rule: this is the product -------------------------------------


def test_a_block_pays_only_when_the_context_is_small_enough():
    """Blocking costs one extra round trip, which re-reads the whole window at
    cache-read rate. Keeping the file costs a cache write plus a re-read on
    every remaining turn. So the block pays exactly while ctx < 40·F — and a
    file guarded at 100k context is the wrong call at 900k."""
    assert block_pays(file_tokens=6_000, context_tokens=200_000)
    assert not block_pays(file_tokens=6_000, context_tokens=300_000)
    assert block_pays(file_tokens=30_000, context_tokens=900_000)


def test_the_break_even_is_forty_times_the_file():
    assert block_pays(file_tokens=1_000, context_tokens=39_000)
    assert not block_pays(file_tokens=1_000, context_tokens=41_000)


def test_the_ratio_is_tunable_because_twenty_remaining_turns_is_an_assumption(monkeypatch):
    monkeypatch.setenv("TOKENDOG_READGUARD_RATIO", "10")
    assert not block_pays(file_tokens=1_000, context_tokens=20_000)
    monkeypatch.setenv("TOKENDOG_READGUARD_RATIO", "not a number")
    assert block_pays(file_tokens=1_000, context_tokens=39_000)   # falls back


def test_an_unknown_context_never_blocks():
    """Without a context size the cost rule cannot be evaluated, and a guess
    that blocks is worse than no guard at all."""
    assert not block_pays(file_tokens=50_000, context_tokens=None)
    assert not block_pays(file_tokens=50_000, context_tokens=0)


# --- sizing ------------------------------------------------------------------


def test_size_is_measured_in_tokens_not_lines(tmp_path):
    """A 387-line markdown file can be 33k tokens; a 5,000-line log can be 4k."""
    wide = _write(tmp_path, "wide.md", ("x" * 900 + "\n") * 40)
    narrow = _write(tmp_path, "narrow.log", "a\n" * 5_000)
    assert estimate_tokens(wide) > estimate_tokens(narrow)


def test_the_char_ratio_is_the_measured_one_not_four(tmp_path):
    p = _write(tmp_path, "f.txt", "x" * 21_000)
    assert estimate_tokens(p) == pytest.approx(21_000 / CHARS_PER_TOKEN, rel=0.01)
    assert CHARS_PER_TOKEN < 4


def test_a_huge_file_is_large_without_being_read(tmp_path):
    """Counting characters in an 800 MB file to decide whether it is big is its
    own denial of service."""
    p = tmp_path / "huge.txt"
    p.write_text("x")
    import os
    os.truncate(p, 9 * 1024 * 1024)
    assert estimate_tokens(p) >= MIN_TOKENS


def test_a_binary_file_is_never_guarded(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"MZ\x00\x90" + b"\xff" * 40_000)
    assert assess({"file_path": str(p)}, context_tokens=50_000).action == "allow"


@pytest.mark.parametrize("ext", sorted(SKIP_EXT))
def test_binary_ish_extensions_are_skipped(tmp_path, ext):
    p = _write(tmp_path, f"f{ext}", "x" * 200_000)
    assert assess({"file_path": str(p)}, context_tokens=50_000).action == "allow"


# --- what counts as a whole-file read ----------------------------------------


def test_a_ranged_read_is_already_the_thing_we_would_ask_for():
    assert not is_whole_file_read({"file_path": "/x/a.py", "offset": 100})
    assert not is_whole_file_read({"file_path": "/x/a.py", "limit": 50})
    assert is_whole_file_read({"file_path": "/x/a.py"})


def test_a_missing_path_is_not_a_read_we_can_judge():
    assert not is_whole_file_read({})


# --- the decision ------------------------------------------------------------


def test_a_large_file_in_a_small_window_is_worth_stopping(tmp_path):
    p = _write(tmp_path, "big.ts", "const x = 1;\n" * 4_000)
    d = assess({"file_path": str(p)}, context_tokens=100_000)
    assert d.action == "suggest"
    assert d.file_tokens > MIN_TOKENS
    assert "offset" in d.reason and "bulk-reader" in d.reason


def test_the_same_file_twice_is_always_allowed(tmp_path):
    """A speed bump, not a wall. Someone who read it anyway meant it, and a
    second refusal just costs another round trip to say no."""
    p = _write(tmp_path, "big.ts", "const x = 1;\n" * 4_000)
    seen = set()
    first = assess({"file_path": str(p)}, context_tokens=100_000, seen=seen)
    assert first.action == "suggest"
    second = assess({"file_path": str(p)}, context_tokens=100_000, seen=seen)
    assert second.action == "allow" and "already" in second.reason


def test_a_small_file_is_not_worth_a_round_trip(tmp_path):
    p = _write(tmp_path, "small.py", "x = 1\n" * 50)
    assert assess({"file_path": str(p)}, context_tokens=10_000).action == "allow"


def test_a_large_file_late_in_a_huge_session_is_allowed(tmp_path):
    """The reason the maths exists: past 40x the file's size, the extra round
    trip costs more than the file ever will. This one is ~25k tokens, so the
    line sits at ~990k — a 1M window late in its life is beyond it."""
    p = _write(tmp_path, "big.ts", "const x = 1;\n" * 4_000)
    d = assess({"file_path": str(p)}, context_tokens=1_100_000)
    assert d.action == "allow" and "cheaper to read it" in d.reason


def test_a_missing_file_is_not_our_business(tmp_path):
    d = assess({"file_path": str(tmp_path / "nope.txt")}, context_tokens=50_000)
    assert d.action == "allow"


def test_a_directory_is_not_a_file_read(tmp_path):
    assert assess({"file_path": str(tmp_path)}, context_tokens=50_000).action == "allow"


# --- modes -------------------------------------------------------------------


def test_off_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_READGUARD_MODE", "off")
    p = _write(tmp_path, "big.ts", "const x = 1;\n" * 4_000)
    assert assess({"file_path": str(p)}, context_tokens=100_000).action == "allow"


def test_shadow_is_the_default_and_never_denies(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKENDOG_READGUARD_MODE", raising=False)
    from tokendog.readguard import mode
    assert mode() == "shadow"


def test_observe_only_forces_shadow(monkeypatch):
    from tokendog.readguard import mode
    monkeypatch.setenv("TOKENDOG_READGUARD_MODE", "enforce")
    monkeypatch.setenv("TOKENDOG_OBSERVE_ONLY", "1")
    assert mode() == "shadow"


def test_a_decision_records_what_it_would_have_saved(tmp_path):
    p = _write(tmp_path, "big.ts", "const x = 1;\n" * 4_000)
    d = assess({"file_path": str(p)}, context_tokens=100_000)
    assert isinstance(d, Decision)
    rec = d.as_record()
    # Numeric and an extension, never the path: this is the same privacy line
    # every other surface holds.
    assert rec["ext"] == ".ts" and rec["file_tokens"] > 0
    assert "file_path" not in rec and str(p) not in repr(rec)


def test_the_ledger_default_is_the_documented_twenty_turns():
    assert DEFAULT_RATIO == 40
