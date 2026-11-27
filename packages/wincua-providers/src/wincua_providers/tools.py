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
    a list of those, or ``{"tool_calls": [...]}``.
    """
    for cand in _json_candidates(text or ""):
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("tool_calls"), list):
            data = data["tool_calls"]
        items = data if isinstance(data, list) else [data]
        calls: list[tuple[str, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else item
            name = fn.get("tool") or fn.get("name")
            args = fn.get("args", fn.get("arguments", fn.get("parameters", {})))
            if isinstance(name, str) and name in TOOL_NAMES:
                calls.append((name, parse_arguments(args)))
        if calls:
            return calls
    return []


def json_mode_instructions(specs: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "Respond with ONLY a JSON object, no prose:",
        '{"tool": "<tool name>", "args": {...}}',
        "Available tools (name(args) — description):",
    ]
    for t in specs or TOOL_SPECS:
        props = ", ".join(t["parameters"].get("properties", {}).keys())
        lines.append(f"- {t['name']}({props}) — {t['description']}")
    return "\n".join(lines)
