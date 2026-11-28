"""Demo A via MCP: the browser form task as a coding agent would drive it (mock backend)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from wincua_driver.backend.mock import MockBackend
from wincua_driver.types import ElementRef
from wincua_mcp.server import build_server, get_runtime, stable_ids
from wincua_mcp.settings import McpSettings

EXPECTED = "OK: Ada Lovelace <ada@example.com>"


class ApproveHitl:
    name = "approve"

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def ask(self, question, *, options=None, risk="info", screenshot=None, timeout_s=300):
        self.questions.append(question)
        return "approve"


async def call(client, name: str, **args: Any) -> dict[str, Any]:
    res = await client.call_tool(name, args)
    assert not res.isError, res.content
    return json.loads(res.content[0].text)


def test_form_task_end_to_end_over_mcp():
    backend = MockBackend()
    hitl = ApproveHitl()
    server = build_server(McpSettings(backend="mock", allow=["msedge.exe"]), backend=backend, hitl=hitl)

    async def go(client):
        launched = await call(client, "launch", url="file:///C:/src/wincua/examples/fixtures/form.html", title="wincua form demo")
        assert launched["ok"] and launched["target"] == "wincua form demo"

        obs = await call(client, "observe", roles=["Edit", "Button"])
        ids = {e["name"]: e["id"] for e in obs["elements"] if e["role"] == "Edit"}
        assert ids == {"Full name": "auto:fullName", "Email": "auto:email"}
        assert any(e["id"] == "auto:submitButton" for e in obs["elements"])

        for eid, text in (("auto:fullName", "Ada Lovelace"), ("auto:email", "ada@example.com")):
            out = await call(client, "act", action="type", element_id=eid, text=text)
            assert out["ok"] and "risk" not in out

        out = await call(client, "act", action="invoke", element_id="auto:submitButton")
        assert out["ok"] and out["confirmation"]["approved"] is True
        assert "Submit form" in hitl.questions[0]

        # The page retitles the window on submit; the target must still resolve.
        after = await call(client, "observe", name_contains="OK:")
        assert after["ok"] is True, after
        assert EXPECTED in [e["name"] for e in after["elements"]]
        assert after["app"]["url"].endswith("#submitted")
        assert EXPECTED in get_runtime(server).current_target

    async def main():
        async with create_connected_server_and_client_session(server) as client:
            await go(client)

    asyncio.run(main())
    assert backend.form_result == EXPECTED


def test_stable_ids_prefer_auto_then_interactive_names():
    refs = [
        ElementRef("label_3", role="Text", name="Full name"),
        ElementRef("fullName_4", role="Edit", name="Full name", auto_id="fullName"),
        ElementRef("label_5", role="Text", name="Phone"),
        ElementRef("edit_6", role="Edit", name="Phone"),
        ElementRef("btn_7", role="Button", name="Submit form"),
        ElementRef("text_8", role="Text", name="Submit form"),
        ElementRef("auto:num1Button", role="Button", name="One"),
        ElementRef("pane_9", role="Pane"),
    ]
    ids = stable_ids(refs)
    assert ids["fullName_4"] == "auto:fullName"
    assert ids["label_3"] == "name:Full name"
    assert ids["edit_6"] == "name:Phone" and ids["label_5"] == "label_5"
    assert ids["btn_7"] == "name:Submit form" and ids["text_8"] == "text_8"
    assert ids["auto:num1Button"] == "auto:num1Button"
    assert ids["pane_9"] == "pane_9"
