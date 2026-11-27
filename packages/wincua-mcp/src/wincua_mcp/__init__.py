"""wincua-mcp — MCP server entrypoint package."""

__version__ = "0.1.0"

# Stable tool names (Phase 1 ship set)
TOOL_NAMES = (
    "session_start",
    "session_end",
    "observe",
    "click",
    "type_text",
    "key",
    "invoke",
    "semantic",
    "ask_human",
    "screenshot",
    "allow_list_set",
)

__all__ = ["TOOL_NAMES", "__version__"]
