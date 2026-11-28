"""Neutral planner tool set + converters to each provider's wire format.

The Agent loop understands these names (see ``wincua.agent.Agent._dispatch_tool``).
``done`` / ``abort`` / ``ask_human`` map to control PlanSteps, everything else
to ``PlanStep(kind="tool")``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from wincua_providers.base import PlanStep

_ELEMENT_ID = {
    "type": "string",
    "description": "Id from the Elements list, copied exactly (e.g. auto:clearButton)",
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "launch",
        "description": (
            "Launch or focus a desktop app. Use app='calculator' for Windows Calculator. "
            "title is the window title to wait for."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App name or executable"},
                "title": {"type": "string", "description": "Expected window title"},
            },
            "required": ["app"],
        },
    },
    {
        "name": "focus",
        "description": "Bring the window with this title to the foreground and observe it.",
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a URL in a new browser window.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "browser": {"type": "string", "enum": ["msedge", "chrome"]},
                "target": {"type": "string", "description": "Expected page/window title"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "invoke",
        "description": (
            "Press a button or menu item. element_id is an id from the Elements list, "
            "e.g. auto:num7Button or name:Clear entry."
        ),
        "parameters": {
            "type": "object",
            "properties": {"element_id": _ELEMENT_ID},
            "required": ["element_id"],
        },
    },
    {
        "name": "type",
        "description": "Type text into the element_id field (or the focused element if omitted).",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "element_id": _ELEMENT_ID,
                "submit": {"type": "boolean", "description": "Press Enter afterwards"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "click",
        "description": "Click an element (element_id) or, only if it has no id, screen coordinates x, y.",
        "parameters": {
            "type": "object",
            "properties": {
                "element_id": _ELEMENT_ID,
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
        },
    },
    {
        "name": "key",
        "description": "Press a key or hotkey, e.g. 'enter', 'escape', 'ctrl+s'.",
        "parameters": {
            "type": "object",
            "properties": {"keys": {"type": "string"}},
            "required": ["keys"],
        },
    },
    {
        "name": "wait",
        "description": "Wait for the UI to settle.",
        "parameters": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
    },
    {
        "name": "ask_human",
        "description": (
            "Ask the human operator a question when the task is ambiguous, risky, "
            "or you are stuck."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question"],
        },
    },
    {
        "name": "done",
        "description": "The task is complete and the result is visible. Summarize the outcome.",
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        "name": "abort",
        "description": "The task cannot be completed. Explain why.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]

TOOL_NAMES = frozenset(t["name"] for t in TOOL_SPECS)


def openai_tools(specs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """OpenAI / llama-server / Ollama ``tools`` format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in (specs or TOOL_SPECS)
    ]


def anthropic_tools(specs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in (specs or TOOL_SPECS)
    ]


_NEST_KEYS = ("args", "arguments", "parameters", "input")
_BATCH_KEYS = ("actions", "calls", "steps", "tool_calls")
MAX_CALLS_PER_REPLY = 20


def _call_name(item: dict[str, Any]) -> str | None:
    fn = item.get("function")
    if isinstance(fn, dict) and fn.get("name") in TOOL_NAMES:
        return str(fn["name"])
    # "name" is also an element-name argument, so only a known tool name counts.
    for key in ("tool", "name"):
        val = item.get(key)
        if isinstance(val, str) and val in TOOL_NAMES:
            return val
    return None


def _call_args(item: dict[str, Any]) -> dict[str, Any]:
    fn = item.get("function")
    if isinstance(fn, dict):
        return parse_arguments(fn.get("arguments"))
    return {k: v for k, v in item.items() if not (k in ("tool", "name") and v in TOOL_NAMES)}


def expand_tool_calls(
    name: str, args: Any, *, limit: int = MAX_CALLS_PER_REPLY
) -> list[tuple[str, dict[str, Any]]]:
    """Flatten one model tool call into an ordered list of calls.

    Small models batch by nesting the next call inside the arguments, e.g.
    ``invoke(element_id=A, tool="invoke", args={"element_id": B, "args": {...}})``
    or by listing calls under ``actions``. That becomes ``[invoke(A), invoke(B), ...]``.
    A stray ``tool`` key naming the tool itself is dropped.
    """
    out: list[tuple[str, dict[str, Any]]] = []

    def walk(tool: str, raw: Any, depth: int) -> None:
        if len(out) >= limit:
            return
        params = dict(parse_arguments(raw))
        stray = params.pop("tool", None)
        if tool not in TOOL_NAMES and isinstance(stray, str) and stray in TOOL_NAMES:
            tool = stray
        if tool not in TOOL_NAMES or depth > 8:
            out.append((tool, params))
            return
        nested: list[tuple[str, Any]] = []
        for key in (*_NEST_KEYS, *_BATCH_KEYS):
            if key not in params:
                continue
            val = params[key]
            items = val if isinstance(val, list) else [val]
            if key in _NEST_KEYS and isinstance(val, str):
                parsed = parse_arguments(val)
                items = [parsed] if parsed else []
                if not parsed:
                    continue
            if not all(isinstance(i, dict) for i in items):
                continue
            params.pop(key)
            for item in items:
                inner_name = _call_name(item)
                inner = _call_args(item)
                if inner_name is None and key in _NEST_KEYS and not _own_args(params):
                    # invoke(args={"element_id": ...}): plain wrapped arguments.
                    params.update({k: v for k, v in inner.items() if k != "tool"})
                    continue
                nested.append((inner_name or tool, inner))
        if _own_args(params) or not nested:
            out.append((tool, params))
        for inner_name, inner in nested:
            walk(inner_name, inner, depth + 1)

    walk(name, args, 0)
    return out[:limit]


def _own_args(params: dict[str, Any]) -> bool:
    return any(v not in (None, "", {}, []) for v in params.values())


def to_plan_steps(name: str, args: Any, call_id: str | None = None) -> list[PlanStep]:
    """``to_plan_step`` for every call in a (possibly nested / batched) model tool call."""
    calls = expand_tool_calls(name, args)
    return [
        to_plan_step(n, a, call_id if i == 0 or not call_id else f"{call_id}_{i}")
        for i, (n, a) in enumerate(calls)
    ]


def to_plan_step(name: str, args: dict[str, Any] | None, call_id: str | None = None) -> PlanStep:
    args = dict(args or {})
    if name == "done":
        return PlanStep(kind="done", name="done", args=args, message=args.get("message"), call_id=call_id)
    if name == "abort":
        msg = args.get("reason") or args.get("message")
        return PlanStep(kind="abort", name="abort", args=args, message=msg, call_id=call_id)
    if name == "ask_human":
        return PlanStep(kind="ask_human", name="ask_human", args=args, call_id=call_id)
    return PlanStep(kind="tool", name=name, args=args, call_id=call_id)


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return val if isinstance(val, dict) else {}
    return {}


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _json_candidates(text: str) -> list[str]:
    out = [m.group(1) for m in _FENCE.finditer(text)]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        out.append(text[start : end + 1])
    return out


def parse_json_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse tool calls a model wrote as JSON text (JSON mode / weak tool support).

    Accepts ``{"tool": ..., "args": {...}}``, ``{"name": ..., "arguments": ...}``,
    a list of those, ``{"actions": [...]}`` / ``{"tool_calls": [...]}``, and calls
    nested inside ``args`` (flattened in order, see ``expand_tool_calls``).
    """
    for cand in _json_candidates(text or ""):
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and _call_name(data) is None:
            batch = next((data[k] for k in _BATCH_KEYS if isinstance(data.get(k), list)), None)
            if batch is not None:
                data = batch
        items = data if isinstance(data, list) else [data]
        calls: list[tuple[str, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _call_name(item)
            if name is None:
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else None
            if fn is not None:
                args: Any = fn.get("arguments")
            else:
                args = next((item[k] for k in _NEST_KEYS if k in item), {})
                rest = {k: v for k, v in item.items() if k not in ("tool", "name", *_NEST_KEYS)}
                if rest:
                    # {"tool": "invoke", "element_id": ...}: arguments at the top level.
                    args = {**rest, **parse_arguments(args)}
            calls.extend(expand_tool_calls(name, args))
        if calls:
            return calls[:MAX_CALLS_PER_REPLY]
    return []


def json_mode_instructions(specs: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "Respond with ONLY a JSON object, no prose. One action:",
        '{"tool": "<tool name>", "args": {...}}',
        "Several actions in order (e.g. pressing several buttons):",
        (
            '{"actions": [{"tool": "invoke", "args": {"element_id": "auto:num1Button"}}, '
            '{"tool": "invoke", "args": {"element_id": "auto:plusButton"}}]}'
        ),
        'Never put a tool call inside "args"; "args" holds only that tool\'s arguments.',
        "Available tools (name(args) — description):",
    ]
    for t in specs or TOOL_SPECS:
        props = ", ".join(t["parameters"].get("properties", {}).keys())
        lines.append(f"- {t['name']}({props}) — {t['description']}")
    return "\n".join(lines)
