"""Regression: Ollama/qwen2.5vl on Win11 Calculator (27 Nov 2026 live run).

The model sent invoke(element_id='clearButton', role=..., auto_id='', target=junk),
which failed with a 120-line UIA dump even though the button was in the tree; then
it pressed Clear forever. This backend runs the real WindowsBackend element walk,
resolution and error path over the fake Win11 Calculator tree.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_PKGS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PKGS / "wincua-providers" / "tests"))
sys.path.insert(0, str(_PKGS / "wincua-driver" / "tests"))

from fake_http import FakeTransport, ollama_tool_reply  # noqa: E402
from test_calc_uia_tree import _FakeNode, build_win11_calculator_tree  # noqa: E402
from wincua import Agent  # noqa: E402
from wincua.config import parse_config  # noqa: E402
from wincua_driver.backend import set_backend  # noqa: E402
from wincua_driver.backend.mock import MockBackend  # noqa: E402
from wincua_driver.backend.windows import WindowsBackend  # noqa: E402
from wincua_driver.types import ActionResult, Observation, Semantic  # noqa: E402


class Win11CalcBackend:
    name = "win11-calc-fake"

    def __init__(self) -> None:
        self.frame = build_win11_calculator_tree()
        wb = object.__new__(WindowsBackend)
        wb.allow = ["calculatorapp.exe"]
        wb.launch_timeout_s = 1.0
        wb.control_timeout_s = 0.3
        wb._wrappers, wb._elements, wb._id_seq = {}, {}, 100
        wb._window, wb._target_title = self.frame, "Calculator"
        wb._search_roots = lambda: [self.frame]
        self.find_window_calls: list[str] = []
        wb._find_window = lambda title, timeout_s=0: self.find_window_calls.append(title)
        self.wb = wb
        self.calc = MockBackend()
        self.pressed: list[str] = []

    def set_allow(self, allow) -> None:
        pass

    def press(self, auto_id: str) -> None:
        self.pressed.append(auto_id)
        self.calc._apply_calc_button(auto_id)

    async def observe(self, target, include) -> Observation:
        # Fresh snapshot ids on every observation, like the live backend.
        uia = self.wb._walk_uia(self.frame)
        return Observation(
            uia=uia,
            app={"target": target, "title": "Calculator", "display": f"Display is {self.calc.display}"},
        )

    async def act(self, action, *, dry_run: bool = False) -> ActionResult:
        if isinstance(action, Semantic) and action.name in {"launch", "wait", "focus"}:
            return ActionResult(ok=True, mode_used="windows")
        return self.wb._act_sync(action, dry_run)


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    be = Win11CalcBackend()
    monkeypatch.setattr(_FakeNode, "invoke", lambda node: be.press(node.element_info.automation_id), raising=False)
    set_backend(be)
    return be


def run_agent(replies: list[dict], **kw):
    t = FakeTransport()
    for r in replies:
        t.add(200, r)
    cfg = parse_config({"provider": {"default": "ollama", "fallback": ["null"]}})
    agent = Agent(
        task="compute 17 + 25",
        provider=cfg.build_provider(transport=t),
        allow=["CalculatorApp.exe"],
        backend="win11-calc-fake",
        observe_target="Calculator",
        max_turns=12,
        **kw,
    )
    return agent, asyncio.run(agent.run()), t


LAUNCH = ollama_tool_reply(("launch", {"app": "calculator"}))


def _invoke(element_id: str, **extra):
    return ("invoke", {"element_id": element_id, **extra})


def test_bare_auto_id_with_junk_args_resolves(backend):
    replies = [
        LAUNCH,
        ollama_tool_reply(_invoke("clearButton", role="Button", name="Clear", auto_id="", target="Calculator_461")),
        ollama_tool_reply(
            _invoke("num1Button_7", target="invoke"),
            _invoke("auto:num7Button"),
            _invoke("Plus"),
            _invoke("num2Button"),
            _invoke("num5Button", name="Five"),
            _invoke("equalButton"),
        ),
        ollama_tool_reply(("done", {"message": "42"})),
    ]
    agent, result, t = run_agent(replies)
    assert result.ok, result.message
    assert backend.pressed == ["clearButton", "num1Button", "num7Button", "plusButton",
                               "num2Button", "num5Button", "equalButton"]
    assert backend.calc.display == "42"
    assert backend.find_window_calls == []  # junk targets were dropped
    prompt = t.requests[1].body["messages"][-1]["content"]
    assert 'auto:clearButton Button "Clear"' in prompt
    assert "clearButton_" not in prompt


def test_pressing_clear_forever_escalates_to_human(backend):
    replies = [LAUNCH] + [
        ollama_tool_reply(_invoke(f"clearButton_{n}", auto_id="", name="Clear", target="Clear entry"))
        for n in (112, 158, 204, 250, 296)
    ]
    agent, result, t = run_agent(replies)
    esc = [s for s in result.steps if s["kind"] == "escalate"]
    assert esc and "same action repeated 3 turns" in esc[0]["reason"] and esc[0]["to"] == "null"
    assert len(t.requests) == 4  # launch + 3 Clear turns, then the human
    assert not result.ok and "HITL" in result.message
    assert backend.pressed == ["clearButton"] * 3


def test_failures_escalate_and_dump_stays_out_of_model_context(backend):
    replies = [LAUNCH] + [ollama_tool_reply(_invoke("percentButton", target="invoke")) for _ in range(5)]
    agent, result, t = run_agent(replies, recorder=True)
    esc = [s for s in result.steps if s["kind"] == "escalate"]
    assert esc and "3 failed actions in a row" in esc[0]["reason"]
    assert len(t.requests) == 4
    model_text = str(t.requests[-1].body["messages"])
    assert "No element for id='auto:percentButton'" in model_text
    assert "UIA tree dump" not in model_text and "--- root[" not in model_text
    failed = [s for s in result.steps if s["kind"] == "tool" and not s["ok"]]
    assert all(len(s["message"]) <= 300 for s in failed)
    log = Path(failed[0]["detail_log"])
    assert "UIA tree dump" in log.read_text(encoding="utf-8")
