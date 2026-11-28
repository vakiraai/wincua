"""JSON-reply mode (models without native tools, e.g. qwen2.5vl:7b): parsing, prompt, payload."""

from __future__ import annotations

import asyncio
import json

import pytest
from fake_http import FakeTransport
from wincua_driver.types import ElementRef, Observation
from wincua_providers import OllamaProvider
from wincua_providers.ollama import lacks_native_tools
from wincua_providers.tools import JSON_REMINDER, parse_json_tool_calls, to_plan_steps

FILL = [
    ("type", {"element_id": "auto:fullName", "text": "Ada Lovelace"}),
    ("type", {"element_id": "auto:email", "text": "ada@example.com"}),
    ("invoke", {"element_id": "auto:submitButton"}),
]


def run(coro):
    return asyncio.run(coro)


def steps(text: str) -> list[tuple[str, dict]]:
    return [(s.name, s.args) for name, args in parse_json_tool_calls(text) for s in to_plan_steps(name, args)]


@pytest.mark.parametrize(
    "text",
    [
        # The format the prompt asks for.
        json.dumps({"actions": [{"tool": n, "args": a} for n, a in FILL]}),
        # Prose and a fence around it.
        "Sure! Here are the steps:\n```json\n" + json.dumps({"actions": [{"tool": n, "args": a} for n, a in FILL]}) + "\n```\nDone.",
        # Several objects, one per line.
        "\n".join(json.dumps({"tool": n, "args": a}) for n, a in FILL),
        # A bare list of OpenAI-style function calls.
        json.dumps([{"function": {"name": n, "arguments": json.dumps(a)}} for n, a in FILL]),
        # Alias keys, tool names and argument names.
        '{"action": "type_text", "element": "auto:fullName", "value": "Ada Lovelace"}'
        '{"action": "fill", "field": "auto:email", "input_text": "ada@example.com"}'
        '{"command": "press_button", "id": "auto:submitButton"}',
        # Whole Elements rows copied as ids.
        json.dumps({"actions": [
            {"tool": "type", "args": {"element_id": 'auto:fullName Edit "Full name"', "text": "Ada Lovelace"}},
            {"tool": "type", "args": {"element_id": '`auto:email`', "text": "ada@example.com"}},
            {"tool": "invoke", "args": {"element_id": "auto:submitButton Button"}},
        ]}),
        # Python-style quotes and trailing commas.
        "{'actions': [{'tool': 'type', 'args': {'element_id': 'auto:fullName', 'text': 'Ada Lovelace'},},"
        " {'tool': 'type', 'args': {'element_id': 'auto:email', 'text': 'ada@example.com'}},"
        " {'tool': 'invoke', 'args': {'element_id': 'auto:submitButton'}},]}",
        # Each call nested in the previous one's args, with top-level arguments.
        '{"tool": "type", "element_id": "auto:fullName", "text": "Ada Lovelace", "args": '
        '{"element_id": "auto:email", "text": "ada@example.com", "args": {"tool": "invoke", "element_id": "auto:submitButton"}}}',
        # Cut off after the third call: the complete ones are kept.
        json.dumps({"actions": [{"tool": n, "args": a} for n, a in FILL]})[:-2] + ', {"tool": "type", "args": {"text": "Ada Lov',
    ],
    ids=["actions", "fenced_prose", "one_per_line", "function_list", "aliases", "row_ids",
         "python_quotes", "nested_toplevel", "cut_off"],
)
def test_qwen25vl_reply_styles_parse_to_the_same_calls(text):
    assert steps(text) == FILL


def test_cut_off_after_a_complete_value_is_closed():
    assert steps('{"tool": "invoke", "args": {"element_id": "auto:submitButton"}') == [FILL[2]]
    assert steps('{"actions": [{"tool": "key", "args": {"keys": "enter"}}') == [("key", {"keys": "enter"})]


def test_cut_off_inside_a_string_never_invents_text():
    assert steps('{"tool": "type", "args": {"element_id": "auto:fullName", "text": "Ada Lov') == []


def test_bare_argument_object_is_inferred_only_when_unambiguous():
    assert steps('{"url": "file:///C:/form.html", "browser": "msedge"}') == [
        ("open_url", {"url": "file:///C:/form.html", "browser": "msedge"})
    ]
    assert steps('{"element_id": "auto:email", "text": "a@b.c"}') == [("type", {"element_id": "auto:email", "text": "a@b.c"})]
    assert steps('{"element_id": "auto:submitButton"}') == [FILL[2]]
    assert steps('{"message": "done?"}') == []
    assert steps('{"x": 1}{"y": 2}') == []


def test_element_names_are_not_mistaken_for_tools():
    assert steps('{"tool": "invoke", "args": {"name": "Open"}}') == [("invoke", {"name": "Open"})]
    assert steps('{"name": "Open"}') == []
    assert steps('{"tool": "rm_rf", "args": {}}') == []
    assert steps("I cannot see the form.") == []


def test_control_aliases_map_to_control_steps():
    (done,) = to_plan_steps(*parse_json_tool_calls('{"tool": "finish", "args": {"result": "42"}}')[0])
    assert done.kind == "done" and done.message == "42"
    (wait,) = to_plan_steps(*parse_json_tool_calls('{"action": "sleep", "duration": "1.5s"}')[0])
    assert wait.args == {"seconds": 1.5}


def form_obs() -> Observation:
    return Observation(
        uia=[
            ElementRef("win", role="Window", name="wincua form demo", bounds=(0, 0, 800, 600)),
            ElementRef("auto:fullName", role="Edit", name="Full name"),
            ElementRef("auto:submitButton", role="Button", name="Submit form", patterns=("Invoke",)),
        ],
        app={"target": "wincua form demo"},
    )


def test_qwen25vl_starts_in_json_mode_with_schema_and_token_cap():
    assert lacks_native_tools("qwen2.5vl:7b") and lacks_native_tools("library/qwen2.5vl")
    assert not lacks_native_tools("qwen2.5:7b") and not lacks_native_tools("qwen3-vl:8b")
    reply = {"message": {"role": "assistant", "content": json.dumps({"actions": [{"tool": "done", "args": {"message": "ok"}}]})}}
    t = FakeTransport().add(200, reply)
    p = OllamaProvider(model="qwen2.5vl:7b", send_screenshot=False, num_ctx=8192, transport=t)
    res = run(p.plan(task="fill the form", observation=form_obs()))
    assert res.steps[0].kind == "done" and len(t.requests) == 1
    body = t.requests[0].body
    assert "tools" not in body
    assert body["format"]["required"] == ["actions"]
    assert "invoke" in body["format"]["properties"]["actions"]["items"]["properties"]["tool"]["enum"]
    assert body["options"] == {"temperature": 0.0, "num_ctx": 8192, "num_predict": 512}
    system, last = body["messages"][0]["content"], body["messages"][-1]
    assert '{"actions": [{"tool": "<tool name>"' in system and 'type {"element_id": "auto:email"' in system
    assert last["content"].endswith(JSON_REMINDER) and "images" not in last
    assert 'auto:fullName Edit "Full name"' in last["content"]


def test_schema_rejected_by_old_ollama_falls_back_to_plain_json():
    ok = {"message": {"role": "assistant", "content": '{"tool": "done", "args": {"message": "ok"}}'}}
    t = FakeTransport().add(400, {"error": "invalid format: expected \"json\" or a valid JSON schema"}).add(200, ok)
    p = OllamaProvider(model="qwen2.5vl:7b", transport=t)
    res = run(p.plan(task="t"))
    assert res.available and res.steps[0].kind == "done"
    assert isinstance(t.requests[0].body["format"], dict) and t.requests[1].body["format"] == "json"
    assert p.json_schema is False


def test_json_history_replays_each_turn_as_one_actions_reply():
    history = [
        {"kind": "tool", "name": "open_url", "args": {"url": "file:///f.html"}, "call_id": "a", "turn": 0, "result": {"ok": True}},
        {"kind": "tool", "name": "type", "args": {"element_id": "auto:fullName", "text": "Ada"}, "call_id": "b", "turn": 1,
         "result": {"ok": True}},
        {"kind": "tool", "name": "invoke", "args": {"element_id": "auto:submitButton", "_cu": 1}, "call_id": "c", "turn": 1,
         "result": {"ok": False, "message": "not found"}},
    ]
    t = FakeTransport().add(200, {"message": {"role": "assistant", "content": "{}"}})
    run(OllamaProvider(model="qwen2.5vl:7b", transport=t).plan(task="t", observation=form_obs(), history=history))
    msgs = t.requests[0].body["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user", "assistant", "user", "user"]
    assert json.loads(msgs[4]["content"]) == {"actions": [
        {"tool": "type", "args": {"element_id": "auto:fullName", "text": "Ada"}},
        {"tool": "invoke", "args": {"element_id": "auto:submitButton"}},
    ]}
    assert msgs[5]["content"] == "Results:\n1. type: ok\n2. invoke: FAILED: not found"
