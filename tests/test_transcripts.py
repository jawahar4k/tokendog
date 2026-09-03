import json

from tokendog.transcripts import (
    event_from_record,
    read_transcript,
    read_transcripts,
    split_cache_creation,
)
from tokendog.event import SOURCE_TRANSCRIPT


USAGE = {
    "input_tokens": 2,
    "cache_creation_input_tokens": 17943,
    "cache_read_input_tokens": 20920,
    "output_tokens": 289,
    "service_tier": "standard",
    "inference_geo": "us",
    "cache_creation": {"ephemeral_1h_input_tokens": 17943, "ephemeral_5m_input_tokens": 0},
}


def _record(usage=None, **over):
    rec = {
        "type": "assistant",
        "timestamp": "2026-09-01T10:00:00Z",
        "sessionId": "sess-1",
        "message": {"model": "claude-opus-5", "usage": usage if usage is not None else USAGE},
    }
    rec.update(over)
    return rec


# --- the ephemeral split -----------------------------------------------


def test_preserves_ephemeral_1h_and_5m_split():
    e = event_from_record(_record())
    assert e.cache_creation_1h_tokens == 17943
    assert e.cache_creation_5m_tokens == 0
    assert e.cache_creation_tokens == 17943


def test_unsplit_scalar_falls_back_to_the_cheaper_bucket():
    total, five_m, one_h = split_cache_creation({"cache_creation_input_tokens": 1000})
    assert (total, five_m, one_h) == (1000, 1000, 0)


def test_split_is_reconciled_against_the_scalar():
    total, five_m, one_h = split_cache_creation({
        "cache_creation_input_tokens": 1000,
        "cache_creation": {"ephemeral_1h_input_tokens": 600, "ephemeral_5m_input_tokens": 0},
    })
    assert (total, five_m, one_h) == (1000, 400, 600)


def test_total_derived_when_only_nested_present():
    total, five_m, one_h = split_cache_creation({
        "cache_creation": {"ephemeral_1h_input_tokens": 10, "ephemeral_5m_input_tokens": 5},
    })
    assert (total, five_m, one_h) == (15, 5, 10)


# --- tier / geo / attribution ------------------------------------------


def test_carries_service_tier_and_inference_geo():
    e = event_from_record(_record())
    assert e.service_tier == "standard"
    assert e.inference_geo == "us"


def test_marks_events_authoritative():
    e = event_from_record(_record())
    assert e.source == SOURCE_TRANSCRIPT
    assert e.is_authoritative


def test_carries_all_four_buckets():
    e = event_from_record(_record())
    assert e.input_tokens == 2
    assert e.output_tokens == 289
    assert e.cache_read_tokens == 20920
    assert e.cache_creation_tokens == 17943


# --- what counts as a turn ---------------------------------------------


def test_record_without_usage_is_not_a_turn():
    assert event_from_record({"type": "user", "message": {"content": "hi"}}) is None
    assert event_from_record({"type": "assistant", "message": {}}) is None
    assert event_from_record({"no_message": True}) is None
    assert event_from_record("not a dict") is None


def test_reads_only_metered_turns_from_a_file(tmp_path):
    f = tmp_path / "sess-a.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "user", "message": {"content": "hi"}}),
        json.dumps(_record()),
        "",
        "{not json",
        json.dumps(_record()),
    ]), encoding="utf-8")
    events = list(read_transcript(f))
    assert len(events) == 2
    assert all(e.event == "assistant-turn" for e in events)


def test_session_id_falls_back_to_filename(tmp_path):
    f = tmp_path / "sess-b.jsonl"
    rec = _record()
    del rec["sessionId"]
    f.write_text(json.dumps(rec), encoding="utf-8")
    assert list(read_transcript(f))[0].session_id == "sess-b"


def test_walks_project_directories(tmp_path):
    (tmp_path / "proj-one").mkdir()
    (tmp_path / "proj-two").mkdir()
    (tmp_path / "proj-one" / "a.jsonl").write_text(json.dumps(_record()), encoding="utf-8")
    (tmp_path / "proj-two" / "b.jsonl").write_text(json.dumps(_record()), encoding="utf-8")
    assert len(list(read_transcripts(tmp_path))) == 2


def test_missing_root_is_not_an_error(tmp_path):
    assert list(read_transcripts(tmp_path / "nope")) == []
