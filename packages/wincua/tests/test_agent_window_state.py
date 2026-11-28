"""Window-state errors (window_minimized, target_obscured, …) reach the model, the human and the run result."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "wincua-providers" / "tests"))

from fake_http import FakeTransport, openai_tool_reply  # noqa: E402
from test_agent_hitl import FakeHuman  # noqa: E402
from wincua import Agent  # noqa: E402
from wincua.config import parse_config  # noqa: E402
from wincua_driver.backend import get_backend, set_backend  # noqa: E402
from wincua_hitl import HumanResponse  # noqa: E402
from wincua_providers import NullProvider, PlanStep, ScriptedProvider  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.chdir(tmp_path)
    set_backend("mock")


def _agent(provider, **kw) -> Agent:
    return Agent(
        task="compute 17 + 25",
        provider=provider,
        allow=["CalculatorApp.exe"],
        backend="mock",
        observe_target="Calculator",
        **kw,
    )


def _issue(code: str, *, sticky: bool = False) -> None:
    backend = get_backend()
    backend.window_issue, backend.window_issue_sticky = code, sticky


def test_scripted_run_stops_with_code_and_hint():
    _issue("window_minimized", sticky=True)
    steps = [PlanStep(kind="tool", name="invoke", args={"auto_id": "num7Button"}), PlanStep(kind="done")]
    result = asyncio.run(_agent(ScriptedProvider(steps)).run())
    assert not result.ok
    assert result.message.startswith("[window_minimized] ")
    assert "Hint: Call focus" in result.message
    failed = next(s for s in result.steps if s.get("name") == "invoke")
    assert failed["code"] == "window_minimized" and failed["hint"]
    observe = next(s for s in result.steps if s["kind"] == "observe")
    assert observe["errors"] == ["window_minimized"]


def test_model_sees_code_and_hint_then_recovers_with_focus():
    _issue("window_minimized")
    t = FakeTransport()
    t.add(200, openai_tool_reply(("invoke", {"auto_id": "num7Button"})))
    t.add(200, openai_tool_reply(("focus", {"title": "Calculator"}), ("invoke", {"auto_id": "num7Button"})))
    t.add(200, openai_tool_reply(("done", {"message": "7 shown"})))
    cfg = parse_config({"provider": {"default": "llamacpp"}})
    result = asyncio.run(_agent(cfg.build_provider(transport=t)).run())
    assert result.ok, result.message
    assert get_backend().display == "7"
    tool_msgs = [m["content"] for m in t.requests[1].body["messages"] if m["role"] == "tool"]
    assert "FAILED: [window_minimized]" in tool_msgs[0]
    assert "hint: Call focus" in tool_msgs[0]


def test_obscured_click_is_shown_to_the_human_with_hint():
    _issue("target_obscured", sticky=True)
    human = FakeHuman(
        [
            HumanResponse(kind="action", action={"tool": "click", "args": {"element_id": "auto:num7Button"}}),
            HumanResponse(kind="abort", text="covered"),
        ]
    )
    result = asyncio.run(_agent(NullProvider(), hitl=human).run())
    assert not result.ok
    question = human.seen[1].question
    assert "Last action click failed [target_obscured]" in question
    assert "Hint: Another window covers the click point" in question


def test_observation_errors_carry_hints_to_the_card():
    _issue("window_cloaked", sticky=True)
    human = FakeHuman([HumanResponse(kind="abort")])
    asyncio.run(_agent(NullProvider(), hitl=human).run())
    errors = human.seen[0].observation["errors"]
    assert errors[0]["code"] == "window_cloaked"
    assert "virtual desktop" in errors[0]["hint"]
