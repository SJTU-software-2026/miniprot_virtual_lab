"""
Backward-compatibility shim — imports from the tools/ package.

All tool-related code now lives in:
  tools/__init__.py        — Package init, TOOL_REGISTRY, CATEGORY_MAP
  tools/bridge.py          — ToolBridge class + get_bridge()
  tools/schemas.py         — Schema normalization
  tools/<category>/        — Tool metadata per category (see TOOL_GUIDE.md)
"""

from .tools import (
    ToolBridge,
    get_bridge,
    normalize_schema,
    TOOL_REGISTRY,
    CATEGORY_MAP,
)

__all__ = [
    "ToolBridge",
    "get_bridge",
    "normalize_schema",
    "TOOL_REGISTRY",
    "CATEGORY_MAP",
]
