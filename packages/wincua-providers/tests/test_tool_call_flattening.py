"""Nested / batched tool calls from small models are flattened into ordered calls."""

from __future__ import annotations

from wincua_providers.tools import (
    expand_tool_calls,
    parse_json_tool_calls,
    to_plan_steps,
)


def test_live_run_nesting_is_flattened_and_stray_tool_key_dropped():
    args = {
        "element_id": "auto:num1Button",
        "tool": "invoke",
        "args": {"element_id": "auto:num7Button", "tool": "invoke", "args": {"element_id": "auto:plusButton"}},
    }
    assert expand_tool_calls("invoke", args) == [
        ("invoke", {"element_id": "auto:num1Button"}),
        ("invoke", {"element_id": "auto:num7Button"}),
        ("invoke", {"element_id": "auto:plusButton"}),
    ]
    assert expand_tool_calls("invoke", {"element_id": "auto:clearButton", "tool": "invoke"}) == [
        ("invoke", {"element_id": "auto:clearButton"})
    ]


def test_wrapped_args_and_action_lists():
    assert expand_tool_calls("invoke", {"args": {"element_id": "auto:num2Button"}}) == [
        ("invoke", {"element_id": "auto:num2Button"})
    ]
    assert expand_tool_calls("invoke", {"args": '{"element_id": "auto:num2Button"}'}) == [
        ("invoke", {"element_id": "auto:num2Button"})
    ]
    batch = {"actions": [{"tool": "invoke", "args": {"element_id": "auto:num2Button"}},
                         {"name": "key", "arguments": {"keys": "enter"}},
                         {"function": {"name": "done", "arguments": '{"message": "ok"}'}}]}
    assert expand_tool_calls("invoke", batch) == [
        ("invoke", {"element_id": "auto:num2Button"}),
        ("key", {"keys": "enter"}),
        ("done", {"message": "ok"}),
    ]


def test_element_name_argument_is_not_mistaken_for_a_tool():
    assert expand_tool_calls("invoke", {"name": "Clear", "args": {"name": "Five"}}) == [
        ("invoke", {"name": "Clear"}),
        ("invoke", {"name": "Five"}),
    ]


def test_unknown_outer_name_uses_stray_tool_key_and_depth_is_capped():
    assert expand_tool_calls("press", {"tool": "invoke", "element_id": "auto:num1Button"}) == [
        ("invoke", {"element_id": "auto:num1Button"})
    ]
    deep: dict = {"element_id": "auto:num1Button"}
    for _ in range(50):
        deep = {"element_id": "auto:num1Button", "args": deep}
    assert len(expand_tool_calls("invoke", deep)) <= 20


def test_json_text_batch_and_nesting():
    text = '{"actions": [{"tool": "invoke", "args": {"element_id": "auto:num1Button"}}, ' \
           '{"tool": "invoke", "args": {"element_id": "auto:num7Button", "tool": "invoke", ' \
           '"args": {"element_id": "auto:plusButton"}}}]}'
    assert [a["element_id"] for _, a in parse_json_tool_calls(text)] == [
        "auto:num1Button", "auto:num7Button", "auto:plusButton"
    ]
    assert parse_json_tool_calls('{"tool": "invoke", "element_id": "auto:num1Button"}') == [
        ("invoke", {"element_id": "auto:num1Button"})
    ]


def test_expanded_steps_get_distinct_call_ids():
    steps = to_plan_steps("invoke", {"element_id": "auto:a", "args": {"element_id": "auto:b"}}, "toolu_1")
    assert [s.call_id for s in steps] == ["toolu_1", "toolu_1_1"]
    assert [s.call_id for s in to_plan_steps("invoke", {"element_id": "auto:a", "args": {"element_id": "auto:b"}})] == [None, None]
