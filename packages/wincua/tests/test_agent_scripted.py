"""Agent observe/act loop with ScriptedProvider (no LLM, no desktop)."""

from __future__ import annotations

import asyncio

from wincua import Agent
from wincua_driver.backend import set_backend
from wincua_providers import NullProvider, ScriptedProvider
from wincua_providers.base import PlanStep


def test_scripted_agent_done_on_mock():
    set_backend("mock")

    async def _run():
        agent = Agent(
            task="noop",
            provider=ScriptedProvider([PlanStep(kind="done", message="ok")]),
            allow=["calc.exe"],
            backend="mock",
        )
        result = await agent.run()
        assert result.ok is True
        assert result.message == "ok"
        assert any(s.get("kind") == "observe" for s in result.steps)

    asyncio.run(_run())


def test_scripted_calculator_on_mock():
    set_backend("mock")
    steps = [
        PlanStep(kind="tool", name="launch", args={"app": "calc.exe", "title": "Calculator"}),
        PlanStep(kind="tool", name="invoke", args={"auto_id": "clearButton"}),
        PlanStep(kind="tool", name="invoke", args={"auto_id": "num1Button"}),
        PlanStep(kind="tool", name="invoke", args={"auto_id": "num7Button"}),
        PlanStep(kind="tool", name="invoke", args={"auto_id": "plusButton"}),
        PlanStep(kind="tool", name="invoke", args={"auto_id": "num2Button"}),
        PlanStep(kind="tool", name="invoke", args={"auto_id": "num5Button"}),
        PlanStep(kind="tool", name="invoke", args={"auto_id": "equalButton"}),
        PlanStep(
            kind="tool",
            name="assert_display",
            args={"target": "Calculator", "equals": "42"},
        ),
        PlanStep(kind="done", message="17 + 25 == 42"),
    ]

    async def _run():
        agent = Agent(
            task="calc 17+25",
            provider=ScriptedProvider(steps),
            allow=["calc.exe", "CalculatorApp.exe", "ApplicationFrameHost.exe"],
            backend="mock",
            observe_target="Calculator",
        )
        result = await agent.run()
        assert result.ok is True, result.message
        asserts = [s for s in result.steps if s.get("name") == "assert_display"]
        assert asserts and asserts[-1]["ok"] is True
        assert "42" in str(asserts[-1].get("display"))

    asyncio.run(_run())


def test_null_provider_fail_closed_without_hitl():
    set_backend("mock")

    async def _run():
        agent = Agent(
            task="needs human",
            provider=NullProvider(),
            hitl=None,
            allow=[],
            backend="mock",
        )
        result = await agent.run()
        assert result.ok is False
        assert result.steps
        assert "HITL" in (result.message or "")

    asyncio.run(_run())
