"""Model-planned browser form run: Agent + fake Ollama + real WindowsBackend on a fake Edge tree."""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_TESTS / "wincua-providers" / "tests"))
sys.path.insert(0, str(_TESTS / "wincua-driver" / "tests"))

from fake_http import FakeTransport, ollama_tool_reply  # noqa: E402
from test_browser_uia_tree import TITLE, Page, make_backend  # noqa: E402
from wincua import Agent  # noqa: E402
from wincua.config import parse_config  # noqa: E402
from wincua_driver.backend import set_backend  # noqa: E402
from wincua_driver.types import ActionResult, Semantic  # noqa: E402

EXPECTED = "OK: Ada Lovelace <ada@example.com>"
OPEN = ollama_tool_reply(("open_url", {"url": "file:///C:/src/wincua/examples/fixtures/form.html", "browser": "msedge", "target": TITLE}))


class EdgeFakeBackend:
    name = "edge-fake"

    def __init__(self) -> None:
        self.page = Page()
        self.wb = make_backend(self.page)
        self.wb._restore_and_focus = lambda window: None
        self.opened = False

    def set_allow(self, allow) -> None:
        self.wb.allow = [a.lower() for a in allow]

    async def observe(self, target, include):
        return self.wb._observe_sync(target, [i for i in include if i != "screenshot"])

    async def act(self, action, *, dry_run: bool = False) -> ActionResult:
        if isinstance(action, Semantic) and action.name == "open_url":
            self.opened = True
            self.wb._bind_window(self.page.frame, title=TITLE)
            return ActionResult(ok=True, mode_used="windows", info={"title": self.page.frame.window_text()})
        return self.wb._act_sync(action, dry_run)


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    be = EdgeFakeBackend()
    set_backend(be)
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # not a TTY: ask_human fails closed
    return be


def run_agent(replies):
    t = FakeTransport()
    for r in replies:
        t.add(200, r)
    cfg = parse_config({"provider": {"default": "ollama", "fallback": ["null"]}})
    agent = Agent(
        task="fill the form",
        provider=cfg.build_provider(transport=t),
        allow=["msedge.exe"],
        backend="edge-fake",
        observe_target=TITLE,
        max_turns=10,
    )
    return asyncio.run(agent.run()), t


def test_model_fills_and_submits_with_auto_ids(backend):
    result, t = run_agent(
        [
            OPEN,
            ollama_tool_reply(
                ("type", {"element_id": "auto:fullName", "text": "Ada Lovelace"}),
                ("type", {"element_id": "auto:email", "text": "ada@example.com"}),
                ("invoke", {"element_id": "auto:submitButton"}),
            ),
            ollama_tool_reply(("done", {"message": EXPECTED})),
        ]
    )
    assert result.ok, result.message
    assert backend.page.submits == 1
    assert backend.page.full_name.value == "Ada Lovelace" and backend.page.email.value == "ada@example.com"
    assert backend.page.address_bar.writes == []
    prompt = t.requests[1].body["messages"][-1]["content"]
    assert 'auto:fullName Edit "Full name"' in prompt
    assert 'auto:submitButton Button "Submit form"' in prompt
    assert "Address and search bar" not in prompt
    final = t.requests[2].body["messages"][-1]["content"]
    assert EXPECTED in final


def test_label_names_and_bare_ids_resolve_to_the_inputs(backend):
    result, t = run_agent(
        [
            OPEN,
            ollama_tool_reply(
                ("type", {"element_id": "Full name", "text": "Ada Lovelace"}),
                ("type", {"element_id": "email", "text": "ada@example.com"}),
                ("invoke", {"element_id": "Submit form"}),
            ),
            ollama_tool_reply(("done", {"message": "submitted"})),
        ]
    )
    assert result.ok, result.message
    assert backend.page.submits == 1
    assert backend.page.result.children()[0].window_text() == EXPECTED


def test_unknown_field_escalates_with_short_errors(backend):
    result, t = run_agent([OPEN] + [ollama_tool_reply(("type", {"element_id": "auto:phone", "text": "555"})) for _ in range(5)])
    esc = [s for s in result.steps if s["kind"] == "escalate"]
    assert esc and "3 failed actions in a row" in esc[0]["reason"] and esc[0]["to"] == "null"
    assert not result.ok
    model_text = str(t.requests[-1].body["messages"])
    assert "No element for id='auto:phone'" in model_text
    assert "UIA tree dump" not in model_text
    failed = [s for s in result.steps if s["kind"] == "tool" and not s["ok"]]
    assert failed and all(len(s["message"]) <= 300 for s in failed)
    assert backend.page.address_bar.writes == []
