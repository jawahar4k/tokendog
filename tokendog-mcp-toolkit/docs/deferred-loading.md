# Deferred tool loading

MCP tool schemas are part of the fixed prompt — paid on every call. Mark rarely-used tools
`deferred` so their schemas load on demand instead of inflating every request.

```python
from tokendog_mcp import DeferredRegistry
reg = DeferredRegistry()

@reg.tool("search", schema=SEARCH_SCHEMA)            # common → eager
def search(q): ...

@reg.tool("export_pdf", schema=PDF_SCHEMA, deferred=True)   # rare → deferred
def export_pdf(id): ...

reg.eager_tools()      # ["search"]  → advertised up front
reg.deferred_tools()   # ["export_pdf"] → schema fetched via reg.load(name) when needed
```

Advertise only `eager_tools()` in your server's tool list; expose a lookup that calls
`reg.load(name)` when a client asks for a deferred tool.
