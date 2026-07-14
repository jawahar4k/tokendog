from tokendog.event import TokenEvent, RUNTIME_CLAUDE, RUNTIME_GLITCH, now_iso

def test_roundtrip():
    e = TokenEvent(ts="2026-07-12T00:00:00+00:00", session_id="s1",
                   runtime=RUNTIME_CLAUDE, event="PostToolUse",
                   input_tokens=10, output_tokens=5, tool="Read")
    line = e.to_json()
    back = TokenEvent.from_json(line)
    assert back == e
    assert back.runtime == "claude-code"

def test_defaults_and_glitch_fields():
    e = TokenEvent(ts="t", session_id="s", runtime=RUNTIME_GLITCH, event="context-load",
                   input_tokens=42, pipeline="feature", run_id="r1", cluster="core")
    assert e.output_tokens == 0 and e.cache_read_tokens == 0
    assert e.pipeline == "feature" and e.cluster == "core" and e.file is None

def test_now_iso_has_tz():
    assert now_iso().endswith("+00:00")
