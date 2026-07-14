# Dense structured schemas

Keep tool descriptions terse and structured — verbose prose in schemas is paid on every call.

```python
from tokendog_mcp import dense_tool_schema
schema = dense_tool_schema("get_issue", "Fetch one issue",
    {"id": ("string", True, "issue id"), "fields": ("array", False, "fields to return")})
```
