def test_import_mcp():
    import wincua_mcp as m

    assert m.__version__
    assert "session_start" in m.TOOL_NAMES
    assert "observe" in m.TOOL_NAMES


def test_server_session_stub():
    import asyncio

    from wincua_mcp.server import WinCUAMCPServer

    async def _run():
        s = WinCUAMCPServer()
        out = await s.call_tool("session_start", {"allow": ["CalculatorApp.exe"]})
        assert out["ok"] is True
        assert out["session_id"]

    asyncio.run(_run())
