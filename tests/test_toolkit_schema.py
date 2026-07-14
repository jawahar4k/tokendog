from tokendog_mcp.schema import dense_tool_schema

def test_builds_schema():
    s = dense_tool_schema("get_issue", "Fetch one issue",
                          {"id": ("string", True, "issue id"),
                           "fields": ("array", False, "fields to return")})
    assert s["name"] == "get_issue"
    props = s["inputSchema"]["properties"]
    assert props["id"]["type"] == "string"
    assert s["inputSchema"]["required"] == ["id"]
    assert "fields" not in s["inputSchema"]["required"]

def test_no_required_params_omits_required_key():
    s = dense_tool_schema("ping", "health check",
                          {"verbose": ("boolean", False, "verbose output")})
    assert "required" not in s["inputSchema"], \
        '"required" key must be absent when no params are required (JSON Schema draft-04 §5.4.3)'
