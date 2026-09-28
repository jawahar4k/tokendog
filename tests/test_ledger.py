"""The setup/work split: what is in the window, by what the reader can do about it."""
import json

import pytest

from tokendog.ledger import (
    SETUP_KINDS,
    WORK_KINDS,
    DEFAULT_RATIO,
    session_split,
)


def _attach(kind, **fields):
    return {"type": "attachment", "attachment": {"type": kind, **fields}}


def _assistant(mid, total, *, text="", thinking="", tools=()):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    if text:
        content.append({"type": "text", "text": text})
    for tid, name in tools:
        content.append({"type": "tool_use", "id": tid, "name": name, "input": {}})
    return {"type": "assistant", "uuid": mid,
            "message": {"id": mid, "content": content,
                        "usage": {"input_tokens": total, "output_tokens": 10,
                                  "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}}


def _result(tid, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def _user(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _write(tmp_path, records, name="s1"):
    p = tmp_path / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


# --- the anchor: totals are exact, only the split is estimated ---------------


def test_the_split_sums_to_the_exact_total_from_usage(tmp_path):
    """The whole contract. A split that does not add up to what the API billed
    is a made-up number wearing a real one's clothes."""
    p = _write(tmp_path, [
        _attach("skill_listing", content="s" * 4000, skillCount=3, names=["a"]),
        _assistant("m1", 50_000),
        _user("hello there"),
        _assistant("m2", 62_000),
    ])
    s = session_split(p)
    assert s.total == 62_000
    assert sum(s.setup.values()) + sum(s.work.values()) == s.total


def test_every_category_is_named_and_none_is_lost(tmp_path):
    p = _write(tmp_path, [_assistant("m1", 1000)])
    s = session_split(p)
    assert set(s.setup) == set(SETUP_KINDS)
    assert set(s.work) == set(WORK_KINDS)


def test_a_zero_total_turn_is_synthetic_and_skipped(tmp_path):
    """Rate-limit notices carry a usage block of zeros. Treating one as a real
    turn reads as the whole window having been thrown away and rebuilt."""
    p = _write(tmp_path, [_assistant("m1", 40_000), _assistant("m2", 0),
                          _assistant("m3", 45_000)])
    assert session_split(p).total == 45_000


def test_records_sharing_one_message_id_settle_once(tmp_path):
    """Several transcript records carry the same API message id. Settling each
    one would count the same growth as many times as the id appears."""
    p = _write(tmp_path, [_assistant("m1", 10_000),
                          _assistant("m1", 10_000),
                          _assistant("m2", 30_000)])
    s = session_split(p)
    assert s.total == 30_000 and s.turns == 2


def test_sidechain_records_are_not_this_session(tmp_path):
    rec = _assistant("m2", 900_000); rec["isSidechain"] = True
    p = _write(tmp_path, [_assistant("m1", 20_000), rec, _assistant("m3", 24_000)])
    assert session_split(p).total == 24_000


# --- what lands where --------------------------------------------------------


def test_setup_is_the_re_sent_definitions(tmp_path):
    """Each of these rides in every turn and is shrunk by disabling something,
    not by /clear."""
    p = _write(tmp_path, [
        _attach("prompt_snapshot", systemPrompt=["p" * 2000], hostPrompt="h"),
        _attach("skill_listing", content="k" * 2000, skillCount=2, names=["a", "b"]),
        _attach("agent_listing_delta", addedLines=["a" * 2000], addedTypes=["x"]),
        _attach("deferred_tools_delta", addedLines=["m" * 2000], addedNames=["T"]),
        _attach("mcp_instructions_delta", addedBlocks=["i" * 2000], addedNames=["srv"]),
        _assistant("m1", 12_000),
    ])
    s = session_split(p)
    for kind in ("sys", "skills", "agents", "mcp"):
        assert s.setup[kind] > 0, kind
    assert sum(s.work.values()) == 0


def test_work_is_the_history(tmp_path):
    p = _write(tmp_path, [
        _assistant("m1", 5_000, tools=[("t1", "Bash")]),
        _result("t1", "o" * 4000),
        _user("u" * 4000),
        _assistant("m2", 40_000, text="a" * 2000),
    ])
    s = session_split(p)
    assert s.work["tools"] > 0 and s.work["chat"] > 0


def test_an_mcp_result_is_work_not_setup(tmp_path):
    """A connector's SCHEMA is setup — it rides every turn. Its RESULT is
    history, and /clear removes it. Same server, opposite lever."""
    p = _write(tmp_path, [
        _assistant("m1", 5_000, tools=[("t1", "mcp__github__search_code")]),
        _result("t1", "r" * 8000),
        _assistant("m2", 40_000),
    ])
    s = session_split(p)
    assert s.work["mcp_results"] > 0
    assert s.setup["mcp"] == 0


def test_growth_with_nothing_visible_is_thinking(tmp_path):
    """Extended thinking is billed and is not in the transcript as content."""
    p = _write(tmp_path, [_assistant("m1", 10_000), _assistant("m2", 40_000)])
    s = session_split(p)
    assert s.work["other"] == 30_000


def test_the_unobserved_first_turn_remainder_is_sys(tmp_path):
    """The system prompt and built-in tool definitions are never in the
    transcript, so the first turn's unexplained bulk is exactly them."""
    p = _write(tmp_path, [
        _attach("skill_listing", content="k" * 400, skillCount=1, names=["a"]),
        _assistant("m1", 30_000),
    ])
    s = session_split(p)
    assert s.setup["sys"] > 25_000
    assert s.setup["skills"] > 0


def test_a_shrink_comes_off_work_not_setup(tmp_path):
    """Setup is re-sent verbatim every turn; it cannot be what went away."""
    p = _write(tmp_path, [
        _assistant("m1", 10_000, tools=[("t1", "Bash")]),
        _result("t1", "o" * 40_000),
        _assistant("m2", 60_000),
        _assistant("m3", 25_000),
    ])
    s = session_split(p)
    before = session_split(_write(tmp_path, [
        _assistant("m1", 10_000, tools=[("t1", "Bash")]),
        _result("t1", "o" * 40_000),
        _assistant("m2", 60_000)], name="s2"))
    assert s.work["tools"] < before.work["tools"]
    assert s.setup["sys"] == before.setup["sys"]


# --- the ratio ---------------------------------------------------------------


def test_the_ratio_defaults_until_enough_is_observed(tmp_path):
    p = _write(tmp_path, [_attach("skill_listing", content="k" * 400, skillCount=1,
                                  names=["a"]), _assistant("m1", 9_000)])
    assert session_split(p).ratio == DEFAULT_RATIO


def test_the_session_measures_its_own_ratio(tmp_path):
    """chars/4 is the naive count; real text is nearer 2.1 chars per token. A
    session that has seen enough content measures the difference for itself."""
    recs = [_assistant("m0", 1_000)]
    total = 1_000
    for i in range(1, 12):
        recs.append(_result(f"t{i}", "word " * 2_000))
        total += 5_000                                   # 10k chars → 2.5k naive
        recs.append(_assistant(f"m{i}", total, tools=[(f"t{i+1}", "Bash")]))
    s = session_split(_write(tmp_path, recs))
    assert s.ratio != DEFAULT_RATIO
    assert 1.0 <= s.ratio <= 4.0


def test_the_ratio_is_clamped_to_a_believable_range(tmp_path):
    """One pathological turn must not make every later split nonsense."""
    recs = [_assistant("m0", 1_000)]
    total = 1_000
    for i in range(1, 12):
        recs.append(_result(f"t{i}", "x" * 40_000))
        total += 50                                       # absurdly cheap text
        recs.append(_assistant(f"m{i}", total, tools=[(f"t{i+1}", "Bash")]))
    assert session_split(_write(tmp_path, recs)).ratio >= 1.0


# --- compaction --------------------------------------------------------------


def test_a_compaction_starts_a_new_segment(tmp_path):
    p = _write(tmp_path, [
        _assistant("m1", 80_000),
        {"type": "system", "subtype": "compact_boundary"},
        _assistant("m2", 20_000),
        _assistant("m3", 30_000),
    ])
    s = session_split(p)
    assert s.segments == 2
    assert s.total == 30_000


def test_sys_is_pinned_to_the_first_segment(tmp_path):
    """Claude Code re-injects setup after a compaction, so the new segment's
    unobserved bulk is not a second system prompt — it is the MCP schemas that
    were loaded on demand earlier. Counting it as sys would double the one
    number the reader uses to decide what to disable."""
    p = _write(tmp_path, [
        _assistant("m1", 30_000),
        {"type": "system", "subtype": "compact_boundary"},
        _assistant("m2", 45_000),
    ])
    s = session_split(p)
    first = session_split(_write(tmp_path, [_assistant("m1", 30_000)], name="s2"))
    assert s.setup["sys"] == first.setup["sys"] == 30_000
    assert s.setup["mcp"] == 15_000
    assert s.total == 45_000


def test_a_rebuilt_window_smaller_than_sys_clips_rather_than_lying(tmp_path):
    """Disable a connector, compact, and the new window can be smaller than the
    system prompt we measured at the start. The total is the fact; the pinned
    estimate is not, so the estimate gives way."""
    p = _write(tmp_path, [
        _assistant("m1", 30_000),
        {"type": "system", "subtype": "compact_boundary"},
        _assistant("m2", 18_000),
    ])
    s = session_split(p)
    assert s.total == 18_000
    assert s.setup["sys"] + s.setup["mcp"] <= 18_000
    assert sum(s.setup.values()) + sum(s.work.values()) == 18_000


# --- robustness --------------------------------------------------------------


def test_a_missing_file_is_an_empty_split_not_a_crash(tmp_path):
    s = session_split(tmp_path / "nope.jsonl")
    assert s.total == 0 and sum(s.setup.values()) == 0


def test_unreadable_lines_are_skipped(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("{not json\n" + json.dumps(_assistant("m1", 5_000)) + "\n", encoding="utf-8")
    assert session_split(p).total == 5_000


@pytest.mark.parametrize("bad", [{"type": "assistant"},
                                 {"type": "assistant", "message": None},
                                 {"type": "attachment", "attachment": "x"}])
def test_malformed_records_do_not_crash(tmp_path, bad):
    p = _write(tmp_path, [bad, _assistant("m1", 5_000)])
    assert session_split(p).total == 5_000
