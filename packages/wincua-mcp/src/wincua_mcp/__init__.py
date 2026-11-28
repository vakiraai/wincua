"""wincua-mcp — MCP server exposing the wincua driver to coding agents."""

__version__ = "0.1.0"

TOOL_NAMES = (
    "observe",
    "act",
    "launch",
    "list_windows",
    "ask_human",
)

__all__ = ["TOOL_NAMES", "__version__"]
