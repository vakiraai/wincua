"""End-to-end: in-process MCP client ↔ FastMCP server ↔ MockBackend."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from wincua_driver.backend.mock import MockBackend
from wincua_driver.types import ImageRef, Invoke, Observation, Semantic
from wincua_mcp import TOOL_NAMES
from wincua_mcp.hitl import StubHitlHook
from wincua_mcp.server import build_server, get_runtime
from wincua_mcp.settings import McpSettings

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class RecordingHitl:
    name = "recording"

    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.questions: list[dict[str, Any]] = []

    async def ask(self, question, *, options=None, risk="info", screenshot=None, timeout_s=300):
        self.questions.append({"question": question, "options": options, "risk": risk, "timeout_s": timeout_s})
        return self.answer


class ScreenshotMock(MockBackend):
    async def observe(self, target, include):
        obs = await super().observe(target, include)
        if "screenshot" in include:
            obs = Observation(uia=obs.uia, app=obs.app, screenshot=ImageRef(data=PNG_1PX, capture_quality="full"))
        return obs


def settings(**kw: Any) -> McpSettings:
    base = dict(backend="mock", allow=["calc.exe", "msedge.exe"])
    base.update(kw)
    return McpSettings(**base)


def run_client(server, fn):
    async def _main():
        async with create_connected_server_and_client_session(server) as client:
            return await fn(client)

    return asyncio.run(_main())


async def call(client, name: str, **args: Any) -> dict[str, Any]:
    res = await client.call_tool(name, args)
    assert not res.isError, res.content
    return json.loads(res.content[0].text)


def test_tools_are_listed_with_schemas():
    server = build_server(settings(), backend=MockBackend(), hitl=StubHitlHook())

    async def go(client):
        tools = (await client.list_tools()).tools
        by_name = {t.name: t for t in tools}
        assert set(by_name) == set(TOOL_NAMES)
        assert by_name["act"].inputSchema["properties"]["action"]["enum"] == ["click", "type", "invoke", "key"]
        assert by_name["observe"].annotations.readOnlyHint is True
        assert by_name["act"].annotations.destructiveHint is True

    run_client(server, go)


def test_list_windows_marks_allowed():
    server = build_server(settings(), backend=MockBackend(), hitl=StubHitlHook())

    async def go(client):
        out = await call(client, "list_windows")
        allowed = {w["title"]: w["allowed"] for w in out["windows"]}
        assert allowed == {
            "Calculator": True,
            "wincua form demo - Microsoft Edge": True,
            "Untitled - Notepad": False,
        }
        only = await call(client, "list_windows", only_allowed=True)
        assert len(only["windows"]) == 2

    run_client(server, go)


def test_empty_allow_list_refuses_everything():
    server = build_server(McpSettings(backend="mock"), backend=MockBackend(), hitl=StubHitlHook())

    async def go(client):
        assert (await call(client, "launch", app="calc"))["error"]["code"] == "allow_list_empty"
        assert (await call(client, "observe", target="Calculator"))["error"]["code"] == "allow_list_empty"
        out = await call(client, "act", action="key", keys="1", target="Calculator")
        assert out["error"]["code"] == "allow_list_empty"

    run_client(server, go)


def test_launch_allow_list():
    backend = MockBackend()
    server = build_server(settings(), backend=backend, hitl=StubHitlHook())

    async def go(client):
        denied = await call(client, "launch", app=r"C:\Windows\notepad.exe")
        assert denied["ok"] is False and denied["error"]["code"] == "allow_list_denied"
        assert backend.actions == []

        assert (await call(client, "launch"))["error"]["code"] == "invalid_args"

        ok = await call(client, "launch", app="calc")
        assert ok["ok"] is True and ok["target"] == "Calculator"
        assert backend.actions[-1] == Semantic("launch", {"app": "calc", "args": []})

        web = await call(client, "launch", url="file:///C:/form.html", title="wincua form demo")
        assert web["ok"] is True and web["target"] == "wincua form demo"
        assert backend.actions[-1].name == "open_url"

        chrome = await call(client, "launch", url="https://example.com", browser="chrome")
        assert chrome["error"]["code"] == "allow_list_denied"

    run_client(server, go)


def test_observe_trims_and_filters():
    server = build_server(settings(max_elements=5), backend=MockBackend(), hitl=StubHitlHook())

    async def go(client):
        res = await client.call_tool("observe", {"target": "Calculator"})
        assert len(res.content) == 1
        out = json.loads(res.content[0].text)
        assert out["ok"] is True
        assert out["target"] == "Calculator"
        assert out["process"] == "ApplicationFrameHost.exe"
        assert len(out["elements"]) == 5 and out["truncated"] is True
        assert out["element_count"] > 5
        assert out["app"]["display"] == "Display is 0"
        assert "screenshot" not in out

        buttons = await call(client, "observe", target="Calculator", roles=["button"], name_contains="seven")
        assert [e["id"] for e in buttons["elements"]] == ["auto:num7Button"]
        assert buttons["elements"][0]["patterns"] == ["Invoke"]

    run_client(server, go)


def test_observe_screenshot_is_image_content():
    server = build_server(settings(), backend=ScreenshotMock(), hitl=StubHitlHook())

    async def go(client):
        res = await client.call_tool("observe", {"target": "Calculator", "include_screenshot": True})
        text, image = res.content
        assert json.loads(text.text)["screenshot"] == {"capture_quality": "full", "attached": True}
        assert image.type == "image" and image.mimeType == "image/png"
        assert base64.b64decode(image.data) == PNG_1PX

    run_client(server, go)


def test_observe_denies_window_outside_allow_list():
    server = build_server(settings(), backend=MockBackend(), hitl=StubHitlHook())

    async def go(client):
        out = await call(client, "observe", target="Notepad")
        assert out["error"]["code"] == "allow_list_denied"
        missing = await call(client, "observe", target="Paint")
        assert missing["error"]["code"] == "window_not_found"
        assert "Calculator" in missing["error"]["details"]["allowed_windows"]

    run_client(server, go)


def test_calculator_end_to_end():
    backend = MockBackend()
    server = build_server(settings(), backend=backend, hitl=StubHitlHook())

    async def go(client):
        assert (await call(client, "launch", app="calc"))["ok"]
        for eid in ["auto:clearButton", "auto:num1Button", "auto:num7Button", "auto:plusButton",
                    "auto:num2Button", "auto:num5Button", "auto:equalButton"]:
            out = await call(client, "act", action="invoke", element_id=eid)
            assert out["ok"] is True, out
            assert "risk" not in out
        obs = await call(client, "observe")
        assert obs["app"]["display"] == "Display is 42"

    run_client(server, go)


def test_act_requires_target_and_valid_args():
    server = build_server(settings(), backend=MockBackend(), hitl=StubHitlHook())

    async def go(client):
        assert (await call(client, "act", action="key", keys="1"))["error"]["code"] == "no_target"
        assert (await call(client, "act", action="click"))["error"]["code"] == "invalid_args"
        assert (await call(client, "act", action="type"))["error"]["code"] == "invalid_args"
        out = await call(client, "act", action="key", keys="1", target="Notepad")
        assert out["error"]["code"] == "allow_list_denied"

    run_client(server, go)


def test_risky_act_denied_without_human_approval():
    backend = MockBackend()
    hitl = RecordingHitl(answer="deny")
    server = build_server(settings(), backend=backend, hitl=hitl)

    async def go(client):
        out = await call(client, "act", action="invoke", element_id="name:Submit form", target="form demo")
        assert out["ok"] is False
        assert out["error"]["code"] == "confirmation_denied"
        assert out["confirmation"] == {"approved": False, "answer": "deny", "hitl": "recording"}
        assert "Submit form" in hitl.questions[0]["question"]
        assert hitl.questions[0]["risk"] == "confirm"
        assert not any(isinstance(a, Invoke) for a in backend.actions)

    run_client(server, go)


def test_risky_act_runs_after_approval():
    backend = MockBackend()
    hitl = RecordingHitl(answer="approve")
    server = build_server(settings(confirm_timeout_s=7), backend=backend, hitl=hitl)

    async def go(client):
        await call(client, "observe", target="form demo")
        out = await call(client, "act", action="invoke", element_id="name:Submit form")
        assert out["ok"] is True
        assert out["confirmation"]["approved"] is True
        assert hitl.questions[0]["timeout_s"] == 7
        assert backend.actions[-1] == Invoke(element_id="name:Submit form")
        assert get_runtime(server).current_target == "wincua form demo - Microsoft Edge"

    run_client(server, go)


def test_stub_hitl_fails_closed():
    backend = MockBackend()
    server = build_server(settings(), backend=backend, hitl=StubHitlHook())

    async def go(client):
        out = await call(client, "act", action="click", x=5, y=5, target="Calculator")
        assert out["error"]["code"] == "confirmation_denied"
        assert out["confirmation"]["hitl"] == "stub"

    run_client(server, go)


def test_confirmation_skipped_when_disabled_or_dry_run():
    hitl = RecordingHitl(answer=None)
    backend = MockBackend()
    server = build_server(settings(require_confirmation=False), backend=backend, hitl=hitl)

    async def go(client):
        out = await call(client, "act", action="type", text="Ada", submit=True, target="form demo")
        assert out["ok"] is True and out["risk"]
        assert hitl.questions == []

    run_client(server, go)

    server2 = build_server(settings(), backend=MockBackend(), hitl=hitl)

    async def go2(client):
        out = await call(client, "act", action="key", keys="alt+f4", target="Calculator", dry_run=True)
        assert out["ok"] is True and out["mode_used"] == "dry-run"
        assert hitl.questions == []

    run_client(server2, go2)


def test_ask_human():
    hitl = RecordingHitl(answer="Edge")
    server = build_server(settings(), backend=MockBackend(), hitl=hitl)

    async def go(client):
        out = await call(client, "ask_human", question="Which browser?", options=["Edge", "Chrome"])
        assert out == {"ok": True, "answered": True, "answer": "Edge", "hitl": "recording"}
        assert hitl.questions[0]["options"] == ["Edge", "Chrome"]

    run_client(server, go)

    server2 = build_server(settings(), backend=MockBackend(), hitl=StubHitlHook())

    async def go2(client):
        out = await call(client, "ask_human", question="Anyone there?")
        assert out["answered"] is False and out["answer"] is None

    run_client(server2, go2)


def test_default_hitl_uses_wincua_hitl():
    server = build_server(settings(), backend=MockBackend())
    assert get_runtime(server).hitl.name == "wincua_hitl"


def test_backend_selected_from_settings():
    server = build_server(settings(backend="mock"))
    rt = get_runtime(server)
    backend = rt.ensure_backend()
    assert isinstance(backend, MockBackend)
    assert backend.allow == ["calc.exe", "msedge.exe"]


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="off-Windows behaviour")
def test_windows_backend_off_windows_reports_error():
    server = build_server(settings(backend="windows"), hitl=StubHitlHook())

    async def go(client):
        out = await call(client, "list_windows")
        assert out["error"]["code"] == "backend_unavailable"

    run_client(server, go)
