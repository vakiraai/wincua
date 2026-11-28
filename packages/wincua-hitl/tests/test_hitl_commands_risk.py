import pytest

from wincua_hitl import parse_command, risk_reason
from wincua_hitl.transport import interpret_text
from wincua_hitl.types import HumanRequest, describe_action, suggest_actions


@pytest.mark.parametrize(
    "text,expected",
    [
        ("invoke auto:num1Button", {"tool": "invoke", "args": {"element_id": "auto:num1Button"}}),
        ("click e12", {"tool": "click", "args": {"element_id": "e12"}}),
        ("click 10 20", {"tool": "click", "args": {"x": 10, "y": 20}}),
        ("type hello world", {"tool": "type", "args": {"text": "hello world"}}),
        ('type "quoted text"', {"tool": "type", "args": {"text": "quoted text"}}),
        ("type @name:Email a@b.c", {"tool": "type", "args": {"element_id": "name:Email", "text": "a@b.c"}}),
        ("key ctrl+s", {"tool": "key", "args": {"keys": "ctrl+s"}}),
        ("wait 0.5", {"tool": "wait", "args": {"seconds": 0.5}}),
        ("launch calculator", {"tool": "launch", "args": {"app": "calculator"}}),
        ("focus Calculator", {"tool": "focus", "args": {"title": "Calculator"}}),
        ("observe", {"tool": "observe", "args": {}}),
    ],
)
def test_parse_command(text, expected):
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", ["", "please add the numbers", "wait soon", "invoke"])
def test_parse_command_rejects(text):
    assert parse_command(text) is None


def test_interpret_text_numbers_keywords_and_free_text():
    req = HumanRequest(
        kind="next_step",
        suggestions=[
            {"label": "One", "tool": "invoke", "args": {"element_id": "auto:num1Button"}},
            {"label": "Type", "tool": "type", "args": {"element_id": "e5", "text": ""}, "needs_text": True},
        ],
    )
    assert interpret_text("1", req).action == {"tool": "invoke", "args": {"element_id": "auto:num1Button"}}
    assert interpret_text("2 hi there", req).action == {"tool": "type", "args": {"element_id": "e5", "text": "hi there"}}
    assert interpret_text("2", req).kind == "instruction"
    assert interpret_text("done all good", req).kind == "done"
    assert interpret_text("takeover", req).kind == "takeover"
    assert interpret_text("key enter", req).action == {"tool": "key", "args": {"keys": "enter"}}
    assert interpret_text("add them up", req).kind == "instruction"


@pytest.mark.parametrize(
    "tool,args",
    [
        ("key", {"keys": "alt+F4"}),
        ("type", {"text": "x", "submit": True}),
        ("semantic", {"name": "excel.set_range"}),
        ("invoke", {"name": "Submit form"}),
        ("invoke", {"element_id": "auto:deleteButton"}),
        ("invoke", {"auto_id": "num1Button", "risk": "confirm"}),
    ],
)
def test_risky(tool, args):
    assert risk_reason(tool, args)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("invoke", {"auto_id": "num1Button"}),
        ("type", {"text": "17"}),
        ("key", {"keys": "enter"}),
        ("wait", {"seconds": 1}),
    ],
)
def test_not_risky(tool, args):
    assert risk_reason(tool, args) is None


def test_suggest_actions_from_mock_observation():
    import asyncio

    from wincua_driver.backend.mock import MockBackend

    obs = asyncio.run(MockBackend().observe("Calculator", ["uia"]))
    sugg = suggest_actions(obs)
    labels = [s["label"] for s in sugg]
    assert labels.count("One") == 1  # deduped by role + name
    assert any(s["tool"] == "type" and s.get("needs_text") for s in sugg)


def test_describe_action_shapes():
    from wincua_driver import Key, Semantic
    from wincua_providers import PlanStep

    assert describe_action(Key(keys="ctrl+s")) == {"tool": "key", "args": {"keys": "ctrl+s"}}
    assert describe_action(Semantic(name="excel.set_range", args={"a": 1})) == {
        "tool": "excel.set_range",
        "args": {"a": 1},
    }
    assert describe_action(PlanStep(kind="tool", name="invoke", args={"x": 1})) == {
        "tool": "invoke",
        "args": {"x": 1},
    }
    assert describe_action({"tool": "click", "args": {}}) == {"tool": "click", "args": {}}


def _win_req():
    from wincua_driver import ElementRef, Observation

    from wincua_hitl.types import observation_to_dict

    obs = Observation(
        uia=[
            ElementRef(element_id="Calculator_1", role="Window", name="Calculator"),
            ElementRef(element_id="CalculatorResults_2", role="Text", name="Display is 0", auto_id="CalculatorResults"),
            ElementRef(element_id="clearButton_7", role="Button", name="Clear", auto_id="clearButton"),
            ElementRef(element_id="clearEntryButton_8", role="Button", name="Clear entry", auto_id="clearEntryButton"),
            ElementRef(element_id="num1Button_123", role="Button", name="One", auto_id="num1Button"),
            ElementRef(element_id="Memory_9", role="Button", name="Memory"),
        ]
    )
    return HumanRequest(kind="next_step", observation=observation_to_dict(obs), suggestions=suggest_actions(obs))


def test_observation_lists_stable_auto_ids():
    req = _win_req()
    ids = [e["element_id"] for e in req.observation["uia"]]
    assert "auto:num1Button" in ids and "num1Button_123" not in ids
    assert "Memory_9" in ids  # no AutomationId: keep the snapshot id
    assert {"label": "One", "tool": "invoke", "args": {"element_id": "auto:num1Button"}} in req.suggestions


@pytest.mark.parametrize(
    "text,eid",
    [
        ("one", "auto:num1Button"),
        ("ONE", "auto:num1Button"),
        ("clear", "auto:clearButton"),
        ("clear entry", "auto:clearEntryButton"),
        ("num1Button", "auto:num1Button"),
        ("invoke one", "auto:num1Button"),
        ("invoke num1button", "auto:num1Button"),
        ("invoke num1Button_999", "auto:num1Button"),
        ("invoke auto:num1Button", "auto:num1Button"),
        ("click One", "auto:num1Button"),
        ("memory", "Memory_9"),
    ],
)
def test_names_and_auto_ids_resolve(text, eid):
    resp = interpret_text(text, _win_req())
    assert resp.kind == "action" and resp.action["args"]["element_id"] == eid


def test_unknown_word_stays_instruction():
    assert interpret_text("please add", _win_req()).kind == "instruction"
    assert interpret_text("type 23", _win_req()).action == {"tool": "type", "args": {"text": "23"}}


def test_chain_with_pipe_or_semicolon():
    req = _win_req()
    for text in ("invoke num1Button_123 | invoke clear", "one; clear", "one ;clear"):
        resp = interpret_text(text, req)
        assert resp.kind == "action", text
        assert [a["args"]["element_id"] for a in resp.actions] == ["auto:num1Button", "auto:clearButton"]
        assert resp.action == resp.actions[0]


def test_chain_with_unknown_part_is_rejected_clearly():
    resp = interpret_text("one | frobnicate", _win_req())
    assert resp.kind == "instruction" and "'frobnicate'" in resp.note and "Nothing was run" in resp.note


def test_type_line_is_not_split():
    resp = interpret_text("type a;b|c", _win_req())
    assert resp.action == {"tool": "type", "args": {"text": "a;b|c"}} and resp.actions is None
