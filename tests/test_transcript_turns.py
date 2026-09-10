"""One metered turn == one API response, not one record.

A single response is written to the transcript as one record per content block
(thinking, text, tool_use), each repeating the same `message.usage`. Counting
records inflated turns and input tokens by ~2x. These tests pin the definition
so it cannot regress quietly again — the previous suite passed throughout the
bug, because nothing asserted the grouping.
"""
import json

import pytest

from tokendog.transcripts import read_transcript, response_key


def _rec(rid, *, blocks, output, ctx=1000, mid=None):
    """One transcript record for response `rid` carrying `output` so far."""
    rec = {
        "type": "assistant",
        "timestamp": "2026-09-07T10:00:00Z",
        "cwd": "/somewhere/projects/demo",
        "message": {
            "model": "test-model",
            "content": [{"type": b} for b in blocks],
            "usage": {"input_tokens": 2, "output_tokens": output,
                      "cache_read_input_tokens": ctx,
                      "cache_creation_input_tokens": 0},
        },
    }
    if rid is not None:
        rec["requestId"] = rid
    if mid is not None:
        rec["message"]["id"] = mid
    return rec


def _write(tmp_path, records, name="s1.jsonl"):
    f = tmp_path / name
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return f


# --- the grouping key --------------------------------------------------


def test_request_id_is_the_key():
    assert response_key({"requestId": "req_1"}) == "req_1"


def test_message_id_is_the_fallback():
    assert response_key({"message": {"id": "msg_1"}}) == "msg_1"


def test_request_id_wins_over_message_id():
    assert response_key({"requestId": "req_1", "message": {"id": "msg_1"}}) == "req_1"


def test_no_identifier_returns_none():
    """None must leave a record ungrouped rather than merge it into a neighbour."""
    assert response_key({"message": {}}) is None
    assert response_key({}) is None
    assert response_key(None) is None


# --- one response, one turn -------------------------------------------


def test_three_records_of_one_response_are_one_turn(tmp_path):
    f = _write(tmp_path, [
        _rec("req_1", blocks=["thinking"], output=3),
        _rec("req_1", blocks=["text"], output=3),
        _rec("req_1", blocks=["tool_use"], output=488),
    ])
    turns = list(read_transcript(f))
    assert len(turns) == 1


def test_context_is_counted_once_not_per_block(tmp_path):
    """The bug: the same 1000-token context billed three times over."""
    f = _write(tmp_path, [
        _rec("req_1", blocks=["thinking"], output=3, ctx=1000),
        _rec("req_1", blocks=["text"], output=3, ctx=1000),
        _rec("req_1", blocks=["tool_use"], output=488, ctx=1000),
    ])
    assert sum(t.context_tokens for t in read_transcript(f)) == 1002


def test_last_record_wins_so_output_is_final_not_partial(tmp_path):
    """Output streams: early records hold partial counts, the last the total."""
    f = _write(tmp_path, [
        _rec("req_1", blocks=["thinking"], output=2),
        _rec("req_1", blocks=["tool_use"], output=490),
    ])
    turns = list(read_transcript(f))
    assert [t.output_tokens for t in turns] == [490]


def test_separate_responses_stay_separate(tmp_path):
    f = _write(tmp_path, [
        _rec("req_1", blocks=["text"], output=10, ctx=1000),
        _rec("req_2", blocks=["text"], output=20, ctx=2000),
        _rec("req_3", blocks=["text"], output=30, ctx=3000),
    ])
    turns = list(read_transcript(f))
    assert len(turns) == 3
    assert [t.output_tokens for t in turns] == [10, 20, 30]
    assert sum(t.context_tokens for t in turns) == 6006


def test_a_single_record_response_is_unaffected(tmp_path):
    f = _write(tmp_path, [_rec("req_1", blocks=["text"], output=42, ctx=500)])
    turns = list(read_transcript(f))
    assert len(turns) == 1
    assert turns[0].output_tokens == 42


def test_emission_order_follows_the_file(tmp_path):
    f = _write(tmp_path, [
        _rec("req_1", blocks=["text"], output=1, ctx=100),
        _rec("req_1", blocks=["tool_use"], output=11, ctx=100),
        _rec("req_2", blocks=["text"], output=22, ctx=200),
    ])
    assert [t.output_tokens for t in read_transcript(f)] == [11, 22]


def test_the_last_response_in_a_file_is_not_dropped(tmp_path):
    """The held response must be flushed at EOF, not left in the buffer."""
    f = _write(tmp_path, [
        _rec("req_1", blocks=["text"], output=5),
        _rec("req_2", blocks=["thinking"], output=1),
        _rec("req_2", blocks=["tool_use"], output=99),
    ])
    turns = list(read_transcript(f))
    assert len(turns) == 2
    assert turns[-1].output_tokens == 99


def test_records_without_an_id_are_each_their_own_turn(tmp_path):
    f = _write(tmp_path, [
        _rec(None, blocks=["text"], output=7, ctx=10),
        _rec(None, blocks=["text"], output=8, ctx=20),
    ])
    turns = list(read_transcript(f))
    assert len(turns) == 2


def test_an_unidentified_record_flushes_the_held_response(tmp_path):
    """It must not be folded into the response that happened to precede it."""
    f = _write(tmp_path, [
        _rec("req_1", blocks=["thinking"], output=1),
        _rec("req_1", blocks=["tool_use"], output=50),
        _rec(None, blocks=["text"], output=9),
    ])
    turns = list(read_transcript(f))
    assert [t.output_tokens for t in turns] == [50, 9]


def test_records_without_usage_are_still_skipped(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "message": {"content": "hello"}},
        _rec("req_1", blocks=["text"], output=4),
        {"type": "assistant", "message": {"content": []}},
    ]) + "\n", encoding="utf-8")
    assert len(list(read_transcript(f))) == 1


def test_a_malformed_line_does_not_drop_the_rest(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps(_rec("req_1", blocks=["text"], output=1)) + "\n"
        + "{not json\n"
        + json.dumps(_rec("req_2", blocks=["text"], output=2)) + "\n",
        encoding="utf-8")
    assert [t.output_tokens for t in read_transcript(f)] == [1, 2]


# --- entrypoint, promoted onto the event ------------------------------


def test_entrypoint_is_captured_onto_the_turn(tmp_path):
    rec = _rec("req_1", blocks=["text"], output=1)
    rec["entrypoint"] = "sdk-cli"
    f = _write(tmp_path, [rec])
    assert list(read_transcript(f))[0].entrypoint == "sdk-cli"


def test_entrypoint_carries_to_later_turns_that_omit_it(tmp_path):
    """It is stated on the opening records, not on every one."""
    opener = _rec("req_1", blocks=["text"], output=1)
    opener["entrypoint"] = "sdk-cli"
    f = _write(tmp_path, [opener, _rec("req_2", blocks=["text"], output=2)])
    assert [t.entrypoint for t in read_transcript(f)] == ["sdk-cli", "sdk-cli"]


def test_a_transcript_with_no_entrypoint_leaves_it_unset(tmp_path):
    f = _write(tmp_path, [_rec("req_1", blocks=["text"], output=1)])
    turn = list(read_transcript(f))[0]
    assert turn.entrypoint is None
    assert turn.is_headless is False


@pytest.mark.parametrize("value,headless", [
    ("cli", False), ("sdk-cli", True), ("sdk", True), (None, False), ("", False),
])
def test_is_headless_reads_the_entrypoint(value, headless):
    """Unknown entrypoints read as interactive — the conservative direction."""
    from tokendog.event import RUNTIME_CLAUDE, SOURCE_TRANSCRIPT, TokenEvent
    e = TokenEvent(ts="2026-09-07T10:00:00Z", session_id="s", runtime=RUNTIME_CLAUDE,
                   event="assistant-turn", source=SOURCE_TRANSCRIPT,
                   cache_read_tokens=10, entrypoint=value)
    assert e.is_headless is headless
