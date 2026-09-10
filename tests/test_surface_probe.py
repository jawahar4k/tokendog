"""The probe is the only thing that launches a server. It must never raise."""
import pytest

from tokendog.surface_probe import (
    DEFAULT_TIMEOUT,
    _reason,
    format_probe,
    probe_connectors,
    tool_tokens,
)


def test_a_tool_definition_is_sized_from_what_the_model_receives():
    small = tool_tokens("a", "short", {"type": "object"})
    big = tool_tokens("a", "x " * 500, {"type": "object", "properties": {}})
    assert 0 < small < big


def test_a_tool_with_no_description_or_schema_still_sizes():
    assert tool_tokens("a", None, None) > 0


def test_an_unserialisable_schema_does_not_raise():
    assert tool_tokens("a", "d", object()) > 0


def test_no_configs_means_no_work():
    assert probe_connectors({}) == ({}, [])


def test_a_missing_command_is_reported_not_raised():
    sizes, results = probe_connectors(
        {"broken": {"command": "definitely-not-a-real-binary-xyz"}}, timeout=5)
    assert sizes == {}
    assert results[0]["ok"] is False
    assert "not found on PATH" in results[0]["error"]


def test_a_config_with_neither_command_nor_url_is_reported():
    sizes, results = probe_connectors({"empty": {}}, timeout=5)
    assert results[0]["ok"] is False


def test_a_hanging_server_is_cut_off_by_the_timeout():
    sizes, results = probe_connectors(
        {"slow": {"command": "sleep", "args": ["30"]}}, timeout=1)
    assert results[0]["ok"] is False
    assert results[0]["ms"] < 20_000


def test_an_exception_group_is_unwrapped_to_a_real_cause():
    """A task-group wrapper says '1 sub-exception' and names nothing."""
    inner = ValueError("the actual problem")
    group = ExceptionGroup("wrapped", [inner])
    assert "the actual problem" in _reason(group)


def test_a_missing_file_explains_the_path_inheritance():
    exc = FileNotFoundError(2, "No such file", "node")
    assert "PATH" in _reason(exc)


def test_deeply_nested_groups_terminate():
    e = ValueError("deep")
    for _ in range(8):
        e = ExceptionGroup("w", [e])
    assert isinstance(_reason(e), str)


def test_the_status_line_distinguishes_reachable_from_not():
    text = format_probe([
        {"connector": "ok", "ok": True, "tools": {"a": 10, "b": 5}, "ms": 12, "error": None},
        {"connector": "bad", "ok": False, "tools": {}, "ms": 3, "error": "nope"},
    ])
    assert "ok: 2 tool(s), 15 tokens" in text
    assert "bad: unreachable — nope" in text


def test_the_default_timeout_is_bounded():
    assert 0 < DEFAULT_TIMEOUT <= 60
