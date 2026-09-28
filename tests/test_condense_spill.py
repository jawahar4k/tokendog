"""Save the full output before cutting it, and point at what was left out."""
import os
import stat

import pytest

from tokendog.condense_spill import (
    RETENTION_DAYS,
    prune,
    save,
    spill_dir,
)

SESSION = "abcd1234-0000-4000-8000-abcdefabcdef"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    return tmp_path


def test_the_full_output_is_on_disk_before_anything_is_cut(home):
    """The contract that makes condensing lossless rather than careful: cutting
    is only allowed once the whole thing is somewhere it can be read back."""
    text = "\n".join(f"line {i}" for i in range(500))
    ref = save(SESSION, text)
    assert ref is not None
    assert open(ref.path, encoding="utf-8").read() == text
    assert ref.lines == 500


def test_the_pointer_says_where_and_how_to_read_it(home):
    ref = save(SESSION, "a\nb\nc\n")
    note = ref.pointer(kept_ranges=[(1, 1)])
    assert ref.path in note
    assert "Read" in note and "offset" in note
    assert "2-3" in note          # the ranges NOT kept, so they can be asked for


def test_the_pointer_lists_every_gap(home):
    ref = save(SESSION, "\n".join(str(i) for i in range(1, 101)))
    note = ref.pointer(kept_ranges=[(1, 10), (60, 70), (95, 100)])
    assert "11-59" in note and "71-94" in note
    assert "1-10" not in note      # kept lines are in the digest already


def test_nothing_was_left_out_says_so(home):
    ref = save(SESSION, "a\nb\n")
    assert "nothing omitted" in ref.pointer(kept_ranges=[(1, 2)]).lower()


def test_the_spill_is_private(home):
    """Tool output is often a log with secrets in it. It is written where only
    the user can read it, or not written at all."""
    ref = save(SESSION, "secret\n")
    assert stat.S_IMODE(os.stat(ref.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(spill_dir()).st_mode) == 0o700


def test_a_bad_session_id_never_steers_the_write(home):
    assert save("../escape", "x\n") is None
    assert save("", "x\n") is None


def test_old_spills_are_pruned(home):
    import time
    ref = save(SESSION, "x\n")
    old = time.time() - (RETENTION_DAYS + 1) * 86400
    os.utime(ref.path, (old, old))
    keep = save(SESSION, "y\n")
    prune()
    assert not os.path.exists(ref.path)
    assert os.path.exists(keep.path)


def test_saving_never_raises_on_a_broken_home(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "not-a-dir"))
    (tmp_path / "not-a-dir").write_text("x")
    assert save(SESSION, "x\n") is None


def test_two_saves_do_not_collide(home):
    a = save(SESSION, "one\n")
    b = save(SESSION, "two\n")
    assert a.path != b.path
    assert open(a.path).read() == "one\n"
