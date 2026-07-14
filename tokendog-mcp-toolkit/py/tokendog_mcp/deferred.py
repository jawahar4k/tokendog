from __future__ import annotations


class DeferredRegistry:
    """Register MCP tools; mark rarely-used ones `deferred` so their schemas
    load on demand instead of inflating the fixed prompt on every call."""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def tool(self, name: str, schema=None, deferred: bool = False):
        def deco(fn):
            self._tools[name] = {"schema": schema, "deferred": deferred, "handler": fn}
            return fn
        return deco

    def eager_tools(self) -> list[str]:
        return [n for n, t in self._tools.items() if not t["deferred"]]

    def deferred_tools(self) -> list[str]:
        return [n for n, t in self._tools.items() if t["deferred"]]

    def _get(self, name: str) -> dict:
        if name not in self._tools:
            raise KeyError(f"Tool {name!r} is not registered. Available: {list(self._tools)}")
        return self._tools[name]

    def load(self, name: str):
        entry = self._get(name)
        if entry["schema"] is None:
            raise ValueError(f"Tool {name!r} was registered without a schema")
        return entry["schema"]

    def call(self, name: str, *args, **kwargs):
        return self._get(name)["handler"](*args, **kwargs)
