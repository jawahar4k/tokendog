"""tokendog_mcp — frugal building blocks for MCP server authors."""
__version__ = "0.1.0"

from .pagination import paginate
from .truncate import cap_items, cap_text
from .batch import run_batch
from .schema import dense_tool_schema
from .deferred import DeferredRegistry

__all__ = ["paginate", "cap_items", "cap_text", "run_batch",
           "dense_tool_schema", "DeferredRegistry"]
