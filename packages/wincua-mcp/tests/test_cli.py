"""CLI flags and a real stdio round-trip through ``python -m wincua_mcp``."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from wincua_mcp.__main__ import main


def test_print_config_applies_overrides(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "wincua.toml"
    cfg.write_text('[driver]\nallow = ["msedge.exe"]\n[mcp]\ntransport = "http"\nhttp_port = 9100\n')
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    rc = main(["--config", str(cfg), "--allow", "calc", "--port", "9200", "--backend", "mock", "--print-config"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "transport: http" in err
    assert "127.0.0.1:9200" in err
    assert "backend: mock" in err
    assert "['msedge.exe', 'calc']" in err


def test_bad_config_exits_2(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.toml")]) == 2
    assert "not found" in capsys.readouterr().err


def test_stdio_round_trip(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "wincua_mcp", "--backend", "mock", "--allow", "calc"],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "WINCUA_CONFIG": ""},
        cwd=str(tmp_path),
    )

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "wincua"
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"observe", "act", "launch", "list_windows", "ask_human"} <= names
                res = await session.call_tool("launch", {"app": "calc"})
                assert json.loads(res.content[0].text)["target"] == "Calculator"

    asyncio.run(asyncio.wait_for(go(), timeout=30))
