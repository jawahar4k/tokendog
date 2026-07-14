import pytest
from tokendog_mcp.deferred import DeferredRegistry


def test_partitions_and_loads():
    reg = DeferredRegistry()

    @reg.tool("common", schema={"x": 1})
    def common(): return "c"

    @reg.tool("rare", schema={"y": 2}, deferred=True)
    def rare(): return "r"

    assert reg.eager_tools() == ["common"]
    assert reg.deferred_tools() == ["rare"]
    assert reg.load("rare") == {"y": 2}   # schema fetched on demand
    assert reg.call("rare") == "r"

def test_load_raises_on_missing_schema():
    reg = DeferredRegistry()

    @reg.tool("no_schema")
    def handler(): return "x"

    with pytest.raises(ValueError, match="registered without a schema"):
        reg.load("no_schema")

def test_load_raises_key_error_with_context_on_unknown_tool():
    reg = DeferredRegistry()

    @reg.tool("known", schema={"a": 1})
    def known(): pass

    with pytest.raises(KeyError, match="not registered"):
        reg.load("unknown_tool")

def test_call_raises_key_error_with_context_on_unknown_tool():
    reg = DeferredRegistry()

    with pytest.raises(KeyError, match="not registered"):
        reg.call("ghost")
