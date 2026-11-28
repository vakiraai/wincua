"""MCP risky-action confirmation and ask_human through the real wincua_hitl LocalUI (loopback HTTP, no browser)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from wincua_driver.backend.mock import MockBackend
from wincua_driver.types import Invoke
from wincua_hitl import CliUI, LocalUI
from wincua_mcp.hitl import WincuaHitlHook
from wincua_mcp.server import build_server
from wincua_mcp.settings import McpSettings


def _http(ui: LocalUI, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(ui.url + path, method="POST" if body is not None else "GET")
    req.add_header("X-Wincua-Token", ui.token)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read() or b"{}")


def _human(ui: LocalUI, payload: dict[str, Any], seen: list[dict[str, Any]]) -> threading.Thread:
    def run() -> None:
        deadline = time.time() + 10
        while time.time() < deadline:
            pending = _http(ui, "/api/state")["pending"]
            if pending:
                seen.append(pending)
                _http(ui, f"/api/respond/{pending['id']}", payload)
                return
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _run(payload: dict[str, Any], fn):
    ui = LocalUI(port=0, open_browser=False, timeout_s=10)
    backend = MockBackend()
    server = build_server(
        McpSettings(backend="mock", allow=["msedge.exe"]), backend=backend, hitl=WincuaHitlHook(ui)
    )
    seen: list[dict[str, Any]] = []

    async def main():
        await ui.start()
        try:
            human = _human(ui, payload, seen)
            async with create_connected_server_and_client_session(server) as client:
                out = await fn(client)
            await asyncio.to_thread(human.join, 5)
            return out
        finally:
            await ui.stop()

    return asyncio.run(main()), seen, backend


async def _act_submit(client):
    res = await client.call_tool(
        "act", {"action": "invoke", "element_id": "name:Submit form", "target": "form demo"}
    )
    return json.loads(res.content[0].text)


def test_risky_act_shows_confirm_card_and_runs_on_approve():
    out, seen, backend = _run({"kind": "approve"}, _act_submit)
    assert seen[0]["kind"] == "confirm"
    assert seen[0]["action"]["tool"] == "invoke"
    assert "Submit form" in seen[0]["reason"]
    assert out["ok"] is True
    assert out["confirmation"] == {"approved": True, "answer": "approve", "hitl": "wincua_hitl"}
    assert backend.actions[-1] == Invoke(element_id="name:Submit form")


def test_risky_act_rejected_in_ui_is_not_run():
    out, _, backend = _run({"kind": "reject"}, _act_submit)
    assert out["error"]["code"] == "confirmation_denied"
    assert out["confirmation"]["approved"] is False
    assert not any(isinstance(a, Invoke) for a in backend.actions)


def test_ask_human_tool_answers_through_local_ui():
    async def ask(client):
        res = await client.call_tool("ask_human", {"question": "Which file?", "options": ["a.txt", "b.txt"]})
        return json.loads(res.content[0].text)

    out, seen, _ = _run({"kind": "answer", "text": "b.txt"}, ask)
    assert seen[0]["question"] == "Which file?"
    assert out == {"ok": True, "answered": True, "answer": "b.txt", "hitl": "wincua_hitl"}


def test_cli_mode_is_replaced_because_stdin_is_the_mcp_channel(tmp_path, monkeypatch):
    cfg = tmp_path / "wincua.toml"
    cfg.write_text('[hitl]\nmode = "cli"\nopen_browser = false\n')
    monkeypatch.setenv("WINCUA_CONFIG", str(cfg))
    transport = WincuaHitlHook().transport
    assert isinstance(transport, LocalUI) and not isinstance(transport, CliUI)
