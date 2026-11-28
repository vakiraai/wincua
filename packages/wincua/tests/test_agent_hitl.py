"""Agent loop + HITL: NullProvider asks every step, risky steps need approval."""

import asyncio

from wincua import Agent
from wincua_driver.backend import set_backend
from wincua_hitl import HitlTransport, HumanResponse
from wincua_providers import NullProvider, PlanStep, ScriptedProvider


class FakeHuman(HitlTransport):
    """Answers requests from a list; records what it was asked."""

    def __init__(self, answers, **kwargs):
        super().__init__(**kwargs)
        self.answers = list(answers)
        self.seen = []

    async def _exchange(self, req, timeout_s):
        self.seen.append(req)
        if not self.answers:
            return None
        a = self.answers.pop(0)
        if isinstance(a, str):
            return HumanResponse(kind="instruction" if req.kind == "next_step" else "answer", text=a)
        return a


def _run(agent):
    return asyncio.run(agent.run())


def _calc_agent(provider, hitl, **kw):
    set_backend("mock")
    return Agent(
        task="compute 17 + 25",
        provider=provider,
        hitl=hitl,
        backend="mock",
        observe_target="Calculator",
        **kw,
    )


def test_null_provider_human_plans_every_step():
    human = FakeHuman(
        [
            "invoke auto:clearButton",
            HumanResponse(kind="action", action={"tool": "invoke", "args": {"element_id": "auto:num1Button"}}),
            "invoke auto:num7Button",
            "invoke auto:plusButton",
            "invoke auto:num2Button",
            "invoke auto:num5Button",
            "invoke auto:equalButton",
            HumanResponse(kind="done", text="42 on screen"),
        ]
    )
    result = _run(_calc_agent(NullProvider(), human))
    assert result.ok is True and result.message == "42 on screen"
    assert all(r.kind == "next_step" for r in human.seen)
    assert human.seen[0].suggestions and human.seen[0].observation["uia"]
    assert "42" in human.seen[-1].observation["app"]["display"]
    assert sum(1 for s in result.steps if s.get("by") == "human") == 7


def test_null_provider_free_text_is_noted_and_failed_action_is_retryable():
    human = FakeHuman(["please add the numbers", "invoke", HumanResponse(kind="abort")])
    result = _run(_calc_agent(NullProvider(), human))
    assert result.ok is False and result.message == "human aborted"
    assert "was not executed" in human.seen[1].question


def test_null_provider_takeover_then_resume_then_done():
    human = FakeHuman(
        [
            HumanResponse(kind="takeover"),
            HumanResponse(kind="resume"),
            HumanResponse(kind="done"),
        ]
    )
    result = _run(_calc_agent(NullProvider(), human))
    assert result.ok is True
    assert [r.kind for r in human.seen] == ["next_step", "takeover", "next_step"]


def test_null_provider_timeout_fails_closed():
    result = _run(_calc_agent(NullProvider(), FakeHuman([])))
    assert result.ok is False and "fail-closed" in result.message


def test_hitl_off_mode_fails_closed():
    result = _run(_calc_agent(NullProvider(), "off"))
    assert result.ok is False and "fail-closed" in result.message


def _risky_script():
    return ScriptedProvider(
        [
            PlanStep(kind="tool", name="invoke", args={"auto_id": "num1Button"}),
            PlanStep(kind="tool", name="key", args={"keys": "alt+f4"}),
            PlanStep(kind="done", message="finished"),
        ]
    )


def test_risky_step_approved():
    human = FakeHuman([HumanResponse(kind="approve")])
    result = _run(_calc_agent(_risky_script(), human))
    assert result.ok is True
    assert len(human.seen) == 1 and human.seen[0].kind == "confirm"
    assert human.seen[0].action == {"tool": "key", "args": {"keys": "alt+f4"}}
    assert any(s.get("name") == "key" for s in result.steps)


def test_risky_step_rejected_is_skipped():
    human = FakeHuman([HumanResponse(kind="reject")])
    result = _run(_calc_agent(_risky_script(), human))
    assert result.ok is True
    assert not any(s.get("name") == "key" for s in result.steps)
    assert any(s.get("request") == "confirm" and s.get("response") == "reject" for s in result.steps)


def test_risky_step_timeout_fails_closed():
    result = _run(_calc_agent(_risky_script(), FakeHuman([])))
    assert result.ok is False and "not confirmed" in result.message


def test_risky_confirm_can_be_disabled():
    human = FakeHuman([])
    result = _run(_calc_agent(_risky_script(), human, confirm_risky=False))
    assert result.ok is True and human.seen == []


def test_planner_flagged_risk_is_confirmed_and_not_forwarded():
    human = FakeHuman([HumanResponse(kind="approve")])
    provider = ScriptedProvider(
        [
            PlanStep(kind="tool", name="invoke", args={"auto_id": "equalButton", "risk": "confirm"}),
            PlanStep(kind="done"),
        ]
    )
    result = _run(_calc_agent(provider, human))
    assert result.ok is True
    assert human.seen[0].reason == "planner marked this step as risky"
    assert "risk" not in human.seen[0].action["args"]


def test_legacy_prompt_only_object_is_adapted():
    class Legacy:
        async def prompt(self, *, question, risk="info"):
            return "abort"

    result = _run(_calc_agent(NullProvider(), Legacy()))
    assert result.ok is False and result.message == "human aborted"


def test_human_invoke_gets_window_target_and_confirmation_note():
    from wincua_driver.backend import get_backend

    human = FakeHuman(["invoke auto:num1Button", HumanResponse(kind="done")])
    result = _run(_calc_agent(NullProvider(), human))
    assert result.ok is True
    sem = [a for a in get_backend().actions if getattr(a, "name", "") == "uia.invoke"]
    assert sem and sem[-1].args["target"] == "Calculator"
    assert "Ran invoke {'element_id': 'auto:num1Button'}: ok." in human.seen[1].question


def test_human_type_focuses_target_window_first():
    from wincua_driver import Semantic, Type
    from wincua_driver.backend import get_backend

    human = FakeHuman(["type 23", HumanResponse(kind="done")])
    result = _run(_calc_agent(NullProvider(), human))
    assert result.ok is True
    acts = get_backend().actions
    i = next(i for i, a in enumerate(acts) if isinstance(a, Type))
    assert isinstance(acts[i - 1], Semantic) and acts[i - 1].name == "focus"
    assert acts[i - 1].args["title"] == "Calculator"


def test_human_failure_note_is_short_and_dump_goes_to_log(tmp_path, monkeypatch):
    from wincua_driver.backend.mock import MockBackend
    from wincua_driver.types import ActionResult, Semantic, StructuredError

    monkeypatch.chdir(tmp_path)
    dump = "\n".join(f"  Button {i}" for i in range(120))
    orig = MockBackend.act

    async def failing(self, action, *, dry_run=False):
        if isinstance(action, Semantic) and action.name == "uia.invoke":
            return ActionResult(
                ok=False,
                mode_used="mock",
                error=StructuredError(code="element_not_found", message=f"No element for id='x'.\nUIA tree dump:\n{dump}"),
            )
        return await orig(self, action, dry_run=dry_run)

    monkeypatch.setattr(MockBackend, "act", failing)
    human = FakeHuman(["invoke x", HumanResponse(kind="abort")])
    _run(_calc_agent(NullProvider(), human))
    note = human.seen[1].question
    assert "No element for id='x'." in note and "Button 5" not in note
    logs = list((tmp_path / "recordings").glob("*-errors.log"))
    assert logs and "Button 119" in logs[0].read_text()
    assert str(logs[0].relative_to(tmp_path)) in note


def test_chained_commands_run_in_order_and_reach_42():
    human = FakeHuman(
        [
            "invoke auto:clearButton | one | invoke auto:num7Button ; plus | two | five | equals",
            HumanResponse(kind="done"),
        ]
    )
    result = _run(_calc_agent(NullProvider(), human))
    assert result.ok is True
    assert sum(1 for s in result.steps if s.get("by") == "human") == 7
    assert "42" in human.seen[-1].observation["app"]["display"]
    assert human.seen[-1].question.count("Ran invoke") == 7


def test_chain_rejected_runs_nothing():
    from wincua_driver.backend import get_backend

    human = FakeHuman(["one | frobnicate", HumanResponse(kind="abort")])
    _run(_calc_agent(NullProvider(), human))
    assert not [a for a in get_backend().actions if getattr(a, "name", "") == "uia.invoke"]
    assert "Nothing was run: 'frobnicate'" in human.seen[1].question
