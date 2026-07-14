# Pagination

Never return an unbounded list from an MCP tool — page it.

```python
from tokendog_mcp import paginate
page = paginate(all_rows, page=1, page_size=50)
# {"items": [...50...], "total": N, "has_more": bool, "next_page": 2 or None}
```

Return `next_page` to the client so it can fetch more only if it needs to.
