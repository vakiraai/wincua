"""Regression: Ollama/qwen2.5vl on Win11 Calculator (27 Nov 2026 live run).

The model sent invoke(element_id='clearButton', role=..., auto_id='', target=junk),
which failed with a 120-line UIA dump even though the button was in the tree; then
it pressed Clear forever. This backend runs the real WindowsBackend element walk,
resolution and error path over the fake Win11 Calculator tree.

Second live run (28 Nov 2026): lookups worked, but the model batched by nesting
calls inside ``args`` (only the outer num1 ran, five turns in a row, on a screen
that kept changing), Invoke then raised COM error -2147220991, and ask_human
without a HITL UI failed closed instead of asking on the terminal.
"""

from __future__ import annotations

import asyncio
import io
import json
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
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # not a TTY: ask_human fails closed
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
    assert esc and "same actions repeated 3 turns" in esc[0]["reason"] and esc[0]["to"] == "null"
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


# --- second live run -----------------------------------------------------------------

def _nested(*element_ids: str) -> dict:
    """invoke(element_id=A, tool='invoke', args={element_id: B, tool: 'invoke', args: {...}})."""
    args: dict = {}
    for eid in reversed(element_ids):
        args = {"element_id": eid, "tool": "invoke", **({"args": args} if args else {})}
    return args


SUM_17_25 = ("auto:num1Button", "auto:num7Button", "auto:plusButton",
             "auto:num2Button", "auto:num5Button", "auto:equalButton")
PRESSED_17_25 = ["num1Button", "num7Button", "plusButton", "num2Button", "num5Button", "equalButton"]


def _json_reply(obj: dict) -> dict:
    return {"model": "qwen2.5vl:7b", "message": {"role": "assistant", "content": json.dumps(obj)}, "done": True}


def run_replies(replies: list[tuple[int, dict]], **kw):
    t = FakeTransport()
    for status, body in replies:
        t.add(status, body)
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


def test_json_mode_nested_batch_is_flattened(backend):
    # qwen2.5vl has no tool support: Ollama refuses tools, wincua switches to JSON replies.
    no_tools = (400, {"error": "registry.ollama.ai/library/qwen2.5vl:7b does not support tools"})
    replies = [
        no_tools,
        (200, _json_reply({"tool": "invoke", "args": {"element_id": "auto:clearButton", "tool": "invoke"}})),
        (200, _json_reply({"tool": "invoke", "args": _nested(*SUM_17_25)})),
        (200, _json_reply({"tool": "done", "args": {"message": "42"}})),
    ]
    agent, result, t = run_replies(replies)
    assert result.ok, result.message
    assert backend.pressed == ["clearButton", *PRESSED_17_25]
    assert backend.calc.display == "42"
    system = t.requests[1].body["messages"][0]["content"]
    assert '{"actions": [' in system and 'Never put a tool call inside "args"' in system
    invokes = next(s for s in result.steps if s["kind"] == "plan" and s["turn"] == 1)["plan_steps"]
    assert [p["args"] for p in invokes] == [{"element_id": e} for e in SUM_17_25]


def test_native_nested_tool_call_is_flattened(backend):
    replies = [
        (200, ollama_tool_reply(("invoke", {"element_id": "auto:clearButton", "tool": "invoke"}))),
        (200, ollama_tool_reply(("invoke", _nested(*SUM_17_25)))),
        (200, ollama_tool_reply(("done", {"message": "42"}))),
    ]
    agent, result, t = run_replies(replies)
    assert result.ok, result.message
    assert backend.pressed == ["clearButton", *PRESSED_17_25]
    # History replays them as six separate calls, none with a nested "args".
    assistant = [m for m in t.requests[2].body["messages"] if m.get("tool_calls")]
    calls = [c["function"]["arguments"] for c in assistant[-1]["tool_calls"]]
    assert [c["element_id"] for c in calls] == list(SUM_17_25)
    assert not any({"args", "tool"} & set(c) for c in calls)


def test_same_action_on_a_changing_screen_escalates(backend):
    # What the live run did once nesting was lost: num1 again and again (display 1, 11, 111...).
    replies = [(200, ollama_tool_reply(_invoke("auto:clearButton")))] + [
        (200, ollama_tool_reply(_invoke("auto:num1Button"))) for _ in range(5)
    ]
    agent, result, t = run_replies(replies)
    esc = [s for s in result.steps if s["kind"] == "escalate"]
    assert esc and esc[0]["to"] == "null" and "same actions repeated 3 turns in a row (" in esc[0]["reason"]
    assert backend.pressed == ["clearButton", "num1Button", "num1Button", "num1Button"]
    assert len(t.requests) == 4


def test_same_digit_twice_in_one_turn_is_not_a_loop(backend):
    keys = ("auto:num2Button", "auto:num2Button", "auto:plusButton",
            "auto:num2Button", "auto:num0Button", "auto:equalButton")
    replies = [
        (200, ollama_tool_reply(*[_invoke(k) for k in keys])),
        (200, ollama_tool_reply(("done", {"message": "42"}))),
    ]
    agent, result, _ = run_replies(replies)
    assert result.ok and backend.calc.display == "42"
    assert not [s for s in result.steps if s["kind"] == "escalate"]


class COMError(Exception):
    """Shaped like comtypes' COMError from the live run."""


def _com_error() -> COMError:
    return COMError(-2147220991, "An event was unable to invoke any of the subscribers", (None, None, None, 0, None))


def test_transient_com_error_on_invoke_is_retried_with_a_fresh_lookup(backend, monkeypatch):
    backend.wb.invoke_retry_delay_s = 0
    failed: list[str] = []

    def flaky_invoke(node):
        auto = node.element_info.automation_id
        if auto == "num5Button" and not failed:
            failed.append(auto)
            raise _com_error()
        backend.press(auto)

    monkeypatch.setattr(_FakeNode, "invoke", flaky_invoke, raising=False)
    replies = [
        (200, ollama_tool_reply(*[_invoke(k) for k in SUM_17_25])),
        (200, ollama_tool_reply(("done", {"message": "42"}))),
    ]
    agent, result, _ = run_replies(replies)
    assert result.ok, result.message
    assert backend.pressed == PRESSED_17_25  # num5 pressed once, after the retry
    five = [s for s in result.steps if s.get("name") == "invoke"][4]
    assert five["ok"] and five["info"]["retried"] is True
    assert five["info"]["invoke_error"] == "UIA_E_ELEMENTNOTAVAILABLE 0x80040201"


def test_invoke_falls_back_to_a_click_when_the_retry_fails(backend, monkeypatch):
    backend.wb.invoke_retry_delay_s = 0

    def broken_invoke(node):
        if node.element_info.automation_id == "num5Button":
            raise _com_error()
        backend.press(node.element_info.automation_id)

    monkeypatch.setattr(_FakeNode, "invoke", broken_invoke, raising=False)
    monkeypatch.setattr(
        _FakeNode, "click_input", lambda node: backend.press(node.element_info.automation_id), raising=False
    )
    replies = [
        (200, ollama_tool_reply(*[_invoke(k) for k in SUM_17_25])),
        (200, ollama_tool_reply(("done", {"message": "42"}))),
    ]
    agent, result, _ = run_replies(replies)
    assert result.ok and backend.calc.display == "42"
    five = [s for s in result.steps if s.get("name") == "invoke"][4]
    assert five["info"]["fallback"] == "click" and "retry_error" in five["info"]


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_null_asks_on_the_terminal_and_hands_the_hint_back_to_the_model(backend, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _TTY("press 4, then 2\n"))
    replies = [(200, ollama_tool_reply(_invoke("auto:num1Button"))) for _ in range(3)] + [
        (200, ollama_tool_reply(("done", {"message": "handed back"}))),
    ]
    agent, result, t = run_replies(replies)
    assert result.ok and result.message == "handed back"
    kinds = [s["kind"] for s in result.steps]
    assert kinds.index("escalate") < kinds.index("ask_human") < kinds.index("resume")
    assert next(s for s in result.steps if s["kind"] == "resume")["to"] == "ollama"
    assert "press 4, then 2" in str(t.requests[-1].body["messages"])
    assert "[wincua] No model available" in capsys.readouterr().out


def test_terminal_answer_abort_ends_the_run(backend, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _TTY("abort\n"))
    replies = [(200, ollama_tool_reply(_invoke("auto:num1Button"))) for _ in range(3)]
    agent, result, _ = run_replies(replies)
    assert not result.ok and result.message == "aborted by human"


def test_terminal_prompt_fails_closed_without_a_tty():
    from wincua._terminal_prompt import ask_on_terminal, classify_answer

    assert asyncio.run(ask_on_terminal("q?", stdin=io.StringIO("hello\n"))) is None
    out = io.StringIO()
    assert asyncio.run(ask_on_terminal("q?", stdin=_TTY("  press 4 \n"), stdout=out)) == "press 4"
    assert "[wincua] q?" in out.getvalue()
    assert [classify_answer(a) for a in ("Done.", "q", "press 4")] == ["done", "abort", "instruction"]
