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


def test_named_sys_re_sent_after_a_compaction_is_not_counted_twice(tmp_path):
    """The prompt snapshot is written to the transcript again after a
    compaction. Pinning it AND counting the new copy doubled sys."""
    snap = _attach("prompt_snapshot", systemPrompt=["p" * 20_000])
    p = _write(tmp_path, [
        snap, _assistant("m1", 30_000),
        {"type": "system", "subtype": "compact_boundary"},
        snap, _assistant("m2", 45_000),
    ])
    s = session_split(p)
    first = session_split(_write(tmp_path, [snap, _assistant("m1", 30_000)], name="s2"))
    assert s.setup["sys"] == first.setup["sys"] == 30_000
    assert s.setup["mcp"] == 15_000


def test_sys_first_seen_only_after_a_compaction_is_not_counted_twice(tmp_path):
    """Claude Code writes the prompt snapshot at compaction, not at the start:
    the first turn measured it as unnamed bulk, the rebuilt segment names it."""
    snap = _attach("prompt_snapshot", systemPrompt=["p" * 20_000])
    p = _write(tmp_path, [
        _assistant("m1", 30_000),
        {"type": "system", "subtype": "compact_boundary"},
        snap, _assistant("m2", 45_000),
    ])
    s = session_split(p)
    assert s.setup["sys"] == 30_000 and s.setup["mcp"] == 15_000


def test_start_up_hook_context_is_sys(tmp_path):
    """A plugin's SessionStart text rides in the prefix like the system prompt."""
    hook = _attach("hook_additional_context", content=["h" * 8_000],
                   hookEvent="SessionStart", hookName="SessionStart")
    s = session_split(_write(tmp_path, [hook, _assistant("m1", 30_000)]))
    assert s.setup["sys"] == 30_000
    p = _write(tmp_path, [
        hook, _assistant("m1", 30_000),
        {"type": "system", "subtype": "compact_boundary"},
        hook, _assistant("m2", 40_000),
    ], name="s3")
    s = session_split(p)
    assert s.setup["sys"] == 30_000 and s.setup["mcp"] == 10_000


def test_a_per_prompt_hook_context_is_not_setup(tmp_path):
    hook = _attach("hook_additional_context", content=["h" * 8_000],
                   hookEvent="UserPromptSubmit")
    s = session_split(_write(tmp_path, [_assistant("m1", 30_000), hook, _assistant("m2", 34_000)]))
    assert s.setup["sys"] == 30_000


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


# --- incremental: a statusline cannot re-read 60 MB on every keystroke -------


def test_resuming_from_a_cached_offset_matches_a_full_parse(tmp_path):
    """The whole point of the offset cache: same answer, new bytes only."""
    from tokendog.ledger import session_split_incremental
    head = [_attach("skill_listing", content="k" * 3000, skillCount=1, names=["a"]),
            _assistant("m1", 40_000, tools=[("t1", "Bash")])]
    tail = [_result("t1", "o" * 8000), _assistant("m2", 55_000),
            _user("u" * 2000), _assistant("m3", 61_000)]
    p = _write(tmp_path, head)
    first, state = session_split_incremental(p)
    with p.open("a", encoding="utf-8") as fh:
        for r in tail:
            fh.write(json.dumps(r) + "\n")
    resumed, _ = session_split_incremental(p, state)
    whole = session_split(p)
    assert resumed.as_dict() == whole.as_dict()
    assert state["offset"] > 0


def test_a_partial_last_line_is_not_parsed_until_it_is_whole(tmp_path):
    """A transcript is appended to while we read it. Half a line is not a record."""
    from tokendog.ledger import session_split_incremental
    p = _write(tmp_path, [_assistant("m1", 20_000)])
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"type": "assistant", "message": {"id": "m2", "usa')
    split, state = session_split_incremental(p)
    assert split.total == 20_000
    with p.open("a", encoding="utf-8") as fh:
        fh.write('ge": {"input_tokens": 33000}, "content": []}}\n')
    resumed, _ = session_split_incremental(p, state)
    assert resumed.total == 33_000


def test_a_rewritten_transcript_starts_over(tmp_path):
    """If the file is now shorter than our offset it is not the file we read."""
    from tokendog.ledger import session_split_incremental
    p = _write(tmp_path, [_assistant("m1", 20_000), _assistant("m2", 90_000)])
    _, state = session_split_incremental(p)
    _write(tmp_path, [_assistant("z1", 7_000)])
    split, _ = session_split_incremental(p, state)
    assert split.total == 7_000


def test_a_resumed_state_does_not_grow_without_bound(tmp_path):
    """State is written to disk on every turn, so it cannot accumulate one entry
    per message id for the life of a session."""
    from tokendog.ledger import session_split_incremental
    recs = []
    for i in range(400):
        recs.append(_assistant(f"m{i}", 1_000 + i * 10))
    p = _write(tmp_path, recs)
    _, state = session_split_incremental(p)
    assert len(json.dumps(state)) < 4_000


# --- the cache the statusline reads -----------------------------------------


def test_refresh_cache_writes_a_split_the_statusline_can_read(tmp_path):
    from tokendog.ledger import refresh_cache, read_cached_split
    p = _write(tmp_path, [_attach("skill_listing", content="k" * 4000, skillCount=1,
                                  names=["a"]),
                          _assistant("m1", 40_000, tools=[("t1", "Bash")]),
                          _result("t1", "o" * 8000),
                          _assistant("m2", 70_000)])
    home = tmp_path / "state"
    out = refresh_cache("sess-0001-aaa", p, home=home)
    assert out["total"] == 70_000
    cached = read_cached_split("sess-0001-aaa", home=home)
    assert cached["setup"]["sys"] > 0 and cached["work"]["tools"] > 0
    assert cached["total"] == 70_000


def test_a_second_refresh_reads_only_the_new_bytes(tmp_path):
    from tokendog.ledger import refresh_cache
    home = tmp_path / "state"
    p = _write(tmp_path, [_assistant("m1", 20_000)])
    refresh_cache("sess-0002-bbb", p, home=home)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_assistant("m2", 50_000)) + "\n")
    out = refresh_cache("sess-0002-bbb", p, home=home)
    assert out["total"] == 50_000
    state = json.loads((home / "split" / "sess-0002-bbb.json").read_text())["state"]
    assert state["offset"] > 0


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "x" * 200, "we;rd$"])
def test_a_session_id_is_validated_before_it_becomes_a_filename(tmp_path, bad):
    """The id comes from the hook payload. It must never steer a write."""
    from tokendog.ledger import refresh_cache, read_cached_split
    p = _write(tmp_path, [_assistant("m1", 9_000)])
    assert refresh_cache(bad, p, home=tmp_path / "state") is None
    assert read_cached_split(bad, home=tmp_path / "state") is None


def test_a_missing_cache_is_none_not_a_crash(tmp_path):
    from tokendog.ledger import read_cached_split
    assert read_cached_split("never-written", home=tmp_path) is None


def test_refresh_never_raises_on_a_bad_transcript(tmp_path):
    from tokendog.ledger import refresh_cache
    assert refresh_cache("sess-0003-ccc", tmp_path / "gone.jsonl", home=tmp_path / "s") is not None


# --- the report --------------------------------------------------------------


def test_format_split_leads_with_the_lever_not_the_percentage():
    from tokendog.report import format_split
    out = format_split({"session": "abcd1234", "project": "demo", "split": {
        "setup": {"sys": 26_000, "mcp": 31_000, "skills": 13_000, "agents": 300},
        "work": {"tools": 90_000, "mcp_results": 0, "skill_results": 0,
                 "chat": 5_000, "other": 1_000},
        "setup_total": 70_300, "work_total": 96_000, "total": 166_300,
        "ratio": 2.1, "turns": 40, "segments": 2}})
    assert "setup" in out.lower() and "work" in out.lower()
    assert "70,300" in out and "96,000" in out and "166,300" in out
    assert "disabl" in out.lower()      # says what moves setup
    assert "2.1" in out                 # the measured ratio is stated, not hidden


def test_newest_transcript_picks_the_most_recently_written(tmp_path):
    from tokendog.transcripts import newest_transcript
    import os, time
    a = tmp_path / "-Users-x-alpha"; a.mkdir()
    b = tmp_path / "-Users-x-beta"; b.mkdir()
    old = a / "old.jsonl"; old.write_text(json.dumps({"cwd": "/Users/x/alpha"}) + "\n")
    new = b / "new.jsonl"; new.write_text(json.dumps({"cwd": "/Users/x/beta"}) + "\n")
    os.utime(old, (time.time() - 500, time.time() - 500))
    assert newest_transcript(root=tmp_path).name == "new.jsonl"
    assert newest_transcript(root=tmp_path, project="alpha").name == "old.jsonl"
    assert newest_transcript(root=tmp_path, project="nope") is None


def test_newest_transcript_on_an_empty_root_is_none(tmp_path):
    from tokendog.transcripts import newest_transcript
    assert newest_transcript(root=tmp_path) is None
