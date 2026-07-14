def test_public_api():
    from tokendog_mcp import (paginate, cap_items, cap_text, run_batch,
                              dense_tool_schema, DeferredRegistry)
    assert callable(paginate) and callable(run_batch) and callable(dense_tool_schema)
    assert isinstance(DeferredRegistry(), DeferredRegistry)
