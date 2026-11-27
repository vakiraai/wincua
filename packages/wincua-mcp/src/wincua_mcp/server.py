"""MCP server stub — tool handlers; stdio transport wired in Phase 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wincua_mcp import TOOL_NAMES


@dataclass
class SessionState:
    session_id: str | None = None
    allow: list[str] = field(default_factory=list)
    mode: str = "foreground"
    recorder: bool = False
    hitl_url: str | None = None
    recording_path: str | None = None


class WinCUAMCPServer:
    """Minimal tool dispatcher. Real MCP SDK/stdio lands in Phase 1."""

    def __init__(self) -> None:
        self.session = SessionState()

    def list_tools(self) -> list[str]:
        return list(TOOL_NAMES)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name == "session_start":
            self.session = SessionState(
                session_id=args.get("session_id") or "stub-session",
                allow=list(args.get("allow") or []),
                mode=str(args.get("mode") or "foreground"),
                recorder=bool(args.get("recorder", False)),
                hitl_url=args.get("hitl_url"),
            )
            return {"ok": True, "session_id": self.session.session_id, "stub": True}

        if name == "session_end":
            path = self.session.recording_path
            sid = self.session.session_id
            self.session = SessionState()
            return {"ok": True, "session_id": sid, "recording_path": path, "stub": True}

        if name == "allow_list_set":
            self.session.allow = list(args.get("allow") or [])
            return {"ok": True, "allow": self.session.allow, "stub": True}

        if name in TOOL_NAMES:
            return {
                "ok": False,
                "stub": True,
                "error": f"Tool {name!r} is registered but not wired to the driver yet.",
                "arguments": args,
            }

        return {"ok": False, "error": f"Unknown tool: {name!r}"}


async def run_stdio() -> None:
    """Placeholder stdio loop — prints tool list; real MCP protocol later."""
    server = WinCUAMCPServer()
    print(
        "wincua-mcp stub: tools = " + ", ".join(server.list_tools()),
        flush=True,
    )
    print(
        "wincua-mcp stub: stdio MCP protocol not wired yet. "
        "Run on interactive Windows session when live.",
        flush=True,
    )
