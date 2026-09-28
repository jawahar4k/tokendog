"""The guard's ledger: what it would have done, and whether that would have paid."""
import json

import pytest

from tokendog.readguard import Decision
from tokendog.readguard_log import record, seen_paths, summary


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    return tmp_path


SESSION = "abcd1234-0000-4000-8000-abcdefabcdef"


def _suggest(tokens=20_000, ctx=100_000, ext=".ts"):
    return Decision("suggest", "…", tokens, ctx, ext)


def test_a_suggestion_is_recorded_with_what_it_would_have_saved(home):
    record(SESSION, _suggest(), mode="shadow")
    s = summary()
    assert s["suggested"] == 1
    # Carrying the file costs a cache write plus ~20 re-reads; the block costs
    # one round trip at cache-read rate. The difference is the saving.
    assert s["would_save"] > 0
    assert s["by_ext"][".ts"]["suggested"] == 1


def test_an_allow_is_recorded_too_because_a_guard_that_never_fires_looks_broken(home):
    record(SESSION, Decision("allow", "small enough to carry", 100, 5_000, ".py"), mode="shadow")
    s = summary()
    assert s["seen"] == 1 and s["suggested"] == 0


def test_nothing_recorded_is_zeroes_not_a_crash(home):
    s = summary()
    assert s["seen"] == 0 and s["suggested"] == 0 and s["would_save"] == 0


def test_the_ledger_never_stores_a_path(home):
    record(SESSION, _suggest(ext=".ts"), mode="shadow")
    text = (home / "readguard.jsonl").read_text()
    assert ".ts" in text and "/" not in json.loads(text.splitlines()[0]).get("ext", "")
    assert "file_path" not in text


def test_seen_paths_is_per_session(home):
    a, b = seen_paths(SESSION), seen_paths("zzzz1234-0000-4000-8000-abcdefabcdef")
    a.add("/x/y.ts")
    assert "/x/y.ts" not in b
    assert "/x/y.ts" in seen_paths(SESSION)


def test_an_unusable_session_id_still_gives_a_usable_set(home):
    """The id comes from a hook payload. A bad one must not make the guard throw."""
    s = seen_paths("../nope")
    s.add("/x/y.ts")
    assert "/x/y.ts" in s


def test_recording_never_raises_even_with_a_broken_home(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "file-not-dir"))
    (tmp_path / "file-not-dir").write_text("x")
    record(SESSION, _suggest(), mode="shadow")     # must not raise


def test_the_summary_is_scoped_to_a_window(home):
    from datetime import datetime, timedelta, timezone
    path = home / "readguard.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    path.write_text(json.dumps({"ts": old, "action": "suggest", "file_tokens": 9_000,
                                "context_tokens": 50_000, "ext": ".ts",
                                "mode": "shadow"}) + "\n", encoding="utf-8")
    record(SESSION, _suggest(), mode="shadow")
    assert summary(days=14)["suggested"] == 1
    assert summary(days=90)["suggested"] == 2


def test_format_names_the_decision_and_charges_it_its_own_cost(home):
    from tokendog.report import format_readguard
    record(SESSION, _suggest(tokens=20_000, ctx=100_000, ext=".ts"), mode="shadow")
    record(SESSION, Decision("allow", "cheaper to read it", 20_000, 900_000, ".py"),
           mode="shadow")
    out = format_readguard(summary(days=14))
    assert "2" in out and ".ts" in out
    assert "net of" in out.lower() or "charged" in out.lower()
    assert "shadow" in out


def test_format_says_nothing_happened_when_nothing_did(home):
    from tokendog.report import format_readguard
    out = format_readguard(summary(days=14))
    assert "no read" in out.lower() or "nothing" in out.lower()
