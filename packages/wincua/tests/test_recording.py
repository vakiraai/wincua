"""recorder=True writes recordings/<session>.jsonl: one line per step, as it happens."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from wincua import Agent
from wincua.session import Session
from wincua_driver.backend import set_backend
from wincua_driver.types import ActionResult, ElementRef, Observation, Semantic, StructuredError
from wincua_providers.base import PlanStep, ProviderResult

LONG_ERROR = "element not found: auto:missing\n" + "\n".join(f"  tree line {i}" for i in range(80))


class FakeBackend:
    name = "rec-fake"

    def set_allow(self, allow) -> None:
        pass

    async def observe(self, target, include):
        return Observation(
            uia=[ElementRef(element_id="auto:go", role="Button", name="Go", auto_id="go")],
            app={"title": target},
            errors=[StructuredError(code="partial_tree", message="2 nodes skipped")],
        )

    async def act(self, action, *, dry_run: bool = False) -> ActionResult:
        args = action.args if isinstance(action, Semantic) else {}
        if args.get("auto_id") == "missing":
            return ActionResult(ok=False, mode_used="mock", error=StructuredError(code="not_found", message=LONG_ERROR))
        return ActionResult(ok=True, mode_used="mock", info={"invoked": args.get("auto_id")})


class ModelLike:
    """Recovers from errors like an LLM planner; replies carry a raw HTTP body."""

    name = "fake-model"
    recovers_from_errors = True

    def __init__(self, turns) -> None:
        self.turns = list(turns)

    async def plan(self, *, task, observation, history):
        text, steps = self.turns.pop(0)
        return ProviderResult(steps=steps, raw={"message": {"role": "assistant", "content": text}}, text=text,
                              provider=self.name)


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_backend(FakeBackend())


def _lines(path: str | None) -> list[dict]:
    assert path, "no recording path"
    return [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines()]


def _run(turns, **kw):
    agent = Agent(task="press Go", provider=ModelLike(turns), backend="rec-fake", observe_target="Fake App",
                  recorder=True, max_turns=5, **kw)
    return asyncio.run(agent.run())


def test_recording_has_raw_reply_parsed_actions_results_and_errors():
    reply1 = '{"actions": [{"tool": "invoke", "args": {"element_id": "auto:missing"}}]}'
    reply2 = '{"actions": [{"tool": "invoke", "args": {"element_id": "auto:go"}}, {"tool": "done"}]}'
    result = _run([
        (reply1, [PlanStep(kind="tool", name="invoke", args={"element_id": "auto:missing"})]),
        (reply2, [PlanStep(kind="tool", name="invoke", args={"element_id": "auto:go"}), PlanStep(kind="done", message="pressed")]),
    ])
    assert result.ok and result.recording_path == f"recordings/{result.steps[0]['session_id']}.jsonl"
    lines = _lines(result.recording_path)
    kinds = [ln["kind"] for ln in lines]
    assert kinds == ["session_start", "task", "observe", "observation", "plan", "tool",
                     "observe", "observation", "plan", "tool", "done", "result"]
    assert all(ln["session"] == result.steps[0]["session_id"] and ln["t"] >= 0 for ln in lines)
    assert lines[0]["provider"] == "fake-model" and lines[1]["task"] == "press Go"

    obs = lines[3]
    assert obs["elements"] == [{"id": "auto:go", "role": "Button", "name": "Go", "auto_id": "go"}]
    assert obs["errors"] == [{"code": "partial_tree", "message": "2 nodes skipped"}]

    plan1, failed, plan2, ok_tool = lines[4], lines[5], lines[8], lines[9]
    assert plan1["raw"]["message"]["content"] == reply1 and plan1["text"] == reply1
    assert plan1["plan_steps"] == [{"kind": "tool", "name": "invoke", "args": {"element_id": "auto:missing"}, "message": None}]
    assert failed["ok"] is False and failed["turn"] == 0 and failed["args"]["auto_id"] == "missing"
    assert failed["detail"] == LONG_ERROR  # the full driver error, not just the short message
    assert failed["detail_log"] and Path(failed["detail_log"]).is_file()
    assert plan2["raw"]["message"]["content"] == reply2
    assert ok_tool["ok"] is True and ok_tool["info"] == {"invoked": "go"}
    assert lines[-1] == {**lines[-1], "kind": "result", "ok": True, "message": "pressed"}
    # In-memory steps keep their old shape: the long detail lives in the log, not the result.
    assert "detail" not in next(s for s in result.steps if s.get("kind") == "tool" and s.get("ok") is False)


def test_failed_run_ends_with_its_result_line():
    result = _run([("I give up", [PlanStep(kind="abort", message="cannot find Go")])])
    assert not result.ok
    last = _lines(result.recording_path)[-1]
    assert last["kind"] == "result" and last["ok"] is False and last["message"] == "cannot find Go"


def test_crash_is_recorded_and_earlier_turns_are_already_on_disk():
    class Boom(ModelLike):
        async def plan(self, **kw):
            if not self.turns:
                raise RuntimeError("provider exploded")
            return await super().plan(**kw)

    agent = Agent(task="press Go", provider=Boom([("ok", [PlanStep(kind="tool", name="invoke", args={"element_id": "auto:go"})])]),
                  backend="rec-fake", observe_target="Fake App", recorder=True, max_turns=5)
    with pytest.raises(RuntimeError):
        asyncio.run(agent.run())
    (path,) = Path("recordings").glob("*.jsonl")
    lines = _lines(str(path))
    assert [ln["kind"] for ln in lines][-3:] == ["observe", "observation", "crash"]
    assert "plan" in [ln["kind"] for ln in lines]
    assert lines[-1]["error"] == "RuntimeError: provider exploded"


def test_no_recorder_writes_nothing():
    agent = Agent(task="t", provider=ModelLike([("d", [PlanStep(kind="done")])]), backend="rec-fake",
                  observe_target="Fake App", recorder=False)
    result = asyncio.run(agent.run())
    assert result.ok and result.recording_path is None and not Path("recordings").exists()


def test_unwritable_recording_is_dropped_not_fatal(tmp_path):
    Path("recordings").write_text("a file, not a folder", encoding="utf-8")
    result = _run([("d", [PlanStep(kind="done", message="fine")])])
    assert result.ok and result.recording_path is None


def test_unserializable_values_are_written_as_text(tmp_path):
    s = Session(recorder=True)
    asyncio.run(s.start())
    s.record({"kind": "x", "obj": object(), "data": b"\x00\x01", "ids": {"a"}})
    (line,) = _lines(s.recording_path)
    assert line["obj"].startswith("<object object") and line["data"] == "<2 bytes>" and line["ids"] == ["a"]
