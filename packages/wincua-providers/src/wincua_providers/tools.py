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
# Keys small models use for the tool name. "name" is left out: it is also an
# element-name argument, so it only counts when it is exactly a tool name.
_TOOL_KEYS = ("tool", "action", "command", "function", "tool_name")
MAX_CALLS_PER_REPLY = 20

TOOL_ALIASES = {
    "type_text": "type", "input_text": "type", "enter_text": "type", "fill": "type",
    "fill_in": "type", "write": "type", "set_text": "type", "set_value": "type",
    "press_button": "invoke", "click_button": "invoke", "invoke_element": "invoke", "submit": "invoke",
    "click_element": "click", "tap": "click",
    "open": "open_url", "open_browser": "open_url", "navigate": "open_url", "goto": "open_url",
    "go_to": "open_url", "browse": "open_url", "open_page": "open_url",
    "launch_app": "launch", "start": "launch", "start_app": "launch", "open_app": "launch",
    "focus_window": "focus", "switch_to": "focus",
    "press": "key", "press_key": "key", "hotkey": "key", "keypress": "key", "send_keys": "key",
    "sleep": "wait", "pause": "wait",
    "finish": "done", "finished": "done", "complete": "done", "completed": "done", "success": "done",
    "fail": "abort", "give_up": "abort", "stop": "abort",
    "ask": "ask_human", "ask_user": "ask_human", "human": "ask_human",
}

# Argument names small models use instead of the tool's own.
_ARG_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "invoke": {"element_id": ("id", "element", "elementId", "element_name", "control", "control_id", "ref")},
    "click": {"element_id": ("id", "element", "elementId", "element_name", "control", "control_id", "ref")},
    "type": {
        "element_id": ("id", "element", "elementId", "element_name", "field", "control", "ref"),
        "text": ("value", "content", "string", "input_text", "keys"),
    },
    "key": {"keys": ("key", "hotkey", "combo", "combination", "text")},
    "open_url": {"url": ("link", "address", "href", "uri", "page"), "target": ("title",)},
    "launch": {"app": ("application", "program", "exe", "name")},
    "focus": {"title": ("window", "target", "name")},
    "wait": {"seconds": ("duration", "time", "secs", "sec", "s")},
    "done": {"message": ("result", "summary", "text", "answer", "output")},
    "abort": {"reason": ("message", "error", "why")},
    "ask_human": {"question": ("text", "message", "prompt", "q")},
}

_ELEMENT_TOOLS = ("invoke", "click", "type")
# `auto:fullName Edit "Full name"` — a whole Elements row copied as the id.
_ROW_TAIL = re.compile(r'\s+[A-Z][A-Za-z]*\s+"[^"]*"\s*$')


def canonical_tool(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    key = key.removeprefix("functions.").removeprefix("tools.")
    if key in TOOL_NAMES:
        return key
    return TOOL_ALIASES.get(key)


def canonical_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Map alias argument names to the tool's own and clean a copied element row."""
    out = dict(args)
    for canon, aliases in _ARG_ALIASES.get(tool, {}).items():
        if out.get(canon) not in (None, ""):
            continue
        for alias in aliases:
            if alias in out and out[alias] not in (None, "") and alias not in _own_params(tool):
                out[canon] = out.pop(alias)
                break
    if tool in _ELEMENT_TOOLS and isinstance(out.get("element_id"), str):
        out["element_id"] = clean_element_id(out["element_id"])
    if tool == "wait" and isinstance(out.get("seconds"), str):
        try:
            out["seconds"] = float(out["seconds"].strip().rstrip("s"))
        except ValueError:
            out.pop("seconds")
    return out


def clean_element_id(eid: str) -> str:
    eid = eid.strip().strip("`'\"").strip()
    if eid.startswith("auto:") and " " in eid:
        return eid.split()[0]
    if eid.startswith("name:"):
        return _ROW_TAIL.sub("", eid)
    return eid


def _own_params(tool: str) -> frozenset[str]:
    spec = next((t for t in TOOL_SPECS if t["name"] == tool), None)
    return frozenset(spec["parameters"].get("properties", {})) if spec else frozenset()


def _call_name(item: dict[str, Any]) -> str | None:
    fn = item.get("function")
    if isinstance(fn, dict):
        return canonical_tool(fn.get("name"))
    for key in _TOOL_KEYS:
        name = canonical_tool(item.get(key))
        if name:
            return name
    val = item.get("name")
    if isinstance(val, str) and val in TOOL_NAMES:
        return val
    return None


def _is_name_key(key: str, value: Any) -> bool:
    if key in _TOOL_KEYS:
        return canonical_tool(value) is not None
    return key == "name" and value in TOOL_NAMES


def _call_args(item: dict[str, Any]) -> dict[str, Any]:
    fn = item.get("function")
    if isinstance(fn, dict):
        return parse_arguments(fn.get("arguments"))
    return {k: v for k, v in item.items() if not _is_name_key(k, v)}


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
    args = canonical_args(name, dict(args or {}))
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


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)(?:```|$)", re.DOTALL)
_OPENERS = re.compile(r"[\[{]")
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_LINE_COMMENT = re.compile(r"(?m)^\s*//.*$")


def _decode_all(text: str) -> list[Any]:
    """Every JSON value in ``text``, left to right (prose, fences, several objects)."""
    decoder = json.JSONDecoder()
    out: list[Any] = []
    pos = 0
    while (m := _OPENERS.search(text, pos)) is not None:
        try:
            value, end = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            pos = m.start() + 1
            continue
        out.append(value)
        pos = end
    return out


def _core(text: str) -> str:
    fenced = _FENCE.search(text)
    body = fenced.group(1) if fenced else text
    start = _OPENERS.search(body)
    return body[start.start():].strip() if start else ""


def _repaired(text: str) -> Any:
    """Best effort for near-JSON: trailing commas, // comments, Python-style quotes."""
    core = _core(text)
    if not core:
        return None
    fixed = _TRAILING_COMMA.sub(r"\1", _LINE_COMMENT.sub("", core))
    for cand in (fixed, _close_brackets(fixed)):
        if cand is None:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
    import ast

    py = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", fixed)))
    try:
        value = ast.literal_eval(py)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, (dict, list)) else None


def _close_brackets(text: str) -> str | None:
    """Append the closers a reply cut off after a complete value is missing.

    None when it was cut inside a string: completing it would invent text.
    """
    stack: list[str] = []
    in_str = escaped = False
    for ch in text:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_str or not stack:
        return None
    return _TRAILING_COMMA.sub(r"\1", text.rstrip().rstrip(",") + "".join(reversed(stack)))


def _calls_from_value(data: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(data, dict) and _call_name(data) is None:
        batch = next((data[k] for k in _BATCH_KEYS if isinstance(data.get(k), list)), None)
        if batch is not None:
            data = batch
    items = data if isinstance(data, list) else [data]
    calls: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        if isinstance(item, list):
            calls.extend(_calls_from_value(item))
            continue
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
            rest = {k: v for k, v in item.items() if k not in _NEST_KEYS and not _is_name_key(k, v)}
            if rest:
                # {"tool": "invoke", "element_id": ...}: arguments at the top level.
                inner = parse_arguments(args)
                if inner.keys() & rest.keys():
                    # The nested args repeat ours: they are the next call, not more arguments.
                    args = {**rest, "args": inner}
                else:
                    args = {**rest, **inner}
        calls.extend(expand_tool_calls(name, args))
    return calls


def _infer_call(data: Any) -> list[tuple[str, dict[str, Any]]]:
    """A bare argument object with no tool name, when its shape names exactly one tool."""
    if not isinstance(data, dict):
        return []
    args = next((data[k] for k in _NEST_KEYS if isinstance(data.get(k), dict)), data)
    keys = {k for k, v in args.items() if v not in (None, "")}
    element = keys & {"element_id", "id", "element", "auto_id"}
    if "url" in keys:
        return [("open_url", dict(args))]
    if element and "text" in keys:
        return [("type", dict(args))]
    if "keys" in keys and not element:
        return [("key", dict(args))]
    if element and keys <= {"element_id", "id", "element", "auto_id", "name", "target"}:
        return [("invoke", dict(args))]
    return []


def parse_json_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse tool calls a model wrote as JSON text (JSON mode / weak tool support).

    Accepts ``{"tool": ..., "args": {...}}``, ``{"name": ..., "arguments": ...}``,
    ``{"action": "type_text", ...}`` (alias keys and names), a list of those,
    ``{"actions": [...]}`` / ``{"tool_calls": [...]}``, several objects in one
    reply, calls nested inside ``args`` (flattened in order, see
    ``expand_tool_calls``), near-JSON (trailing commas, single quotes) and a
    reply cut off mid-way (the complete calls before the cut are kept).
    """
    text = text or ""
    values = _decode_all(text)
    calls = [c for v in values for c in _calls_from_value(v)]
    if not calls:
        repaired = _repaired(text)
        if repaired is not None:
            values = [repaired]
            calls = _calls_from_value(repaired)
    if not calls and len(values) == 1:
        calls = _infer_call(values[0])
    return calls[:MAX_CALLS_PER_REPLY]


_ARG_EXAMPLES = {
    "launch": '{"app": "calculator", "title": "Calculator"}',
    "focus": '{"title": "Calculator"}',
    "open_url": '{"url": "https://example.com", "browser": "msedge", "target": "Example"}',
    "invoke": '{"element_id": "auto:submitButton"}',
    "type": '{"element_id": "auto:email", "text": "ada@example.com"}',
    "click": '{"element_id": "name:OK"}',
    "key": '{"keys": "enter"}',
    "wait": '{"seconds": 1}',
    "ask_human": '{"question": "Which file?"}',
    "done": '{"message": "The page shows the result"}',
    "abort": '{"reason": "The app is not installed"}',
}

JSON_REMINDER = (
    'Reply with ONLY this JSON: {"actions": [{"tool": "<tool>", "args": {...}}, ...]}. '
    "Copy element_id values exactly from the Elements list. Do not repeat actions that already succeeded."
)


def json_mode_instructions(specs: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "Reply format: ONLY a JSON object, no prose, no code fences:",
        '{"actions": [{"tool": "<tool name>", "args": {<that tool\'s arguments>}}]}',
        "List one or more actions, in the order to run them. Example (fill two fields, then press a button):",
        (
            '{"actions": [{"tool": "type", "args": {"element_id": "auto:fullName", "text": "Ada"}}, '
            '{"tool": "type", "args": {"element_id": "auto:email", "text": "ada@example.com"}}, '
            '{"tool": "invoke", "args": {"element_id": "auto:submitButton"}}]}'
        ),
        'Each "args" holds only that tool\'s arguments, never another tool call.',
        "Tools and their args:",
    ]
    for t in specs or TOOL_SPECS:
        example = _ARG_EXAMPLES.get(t["name"])
        if example is None:
            example = "{" + ", ".join(f'"{p}": ...' for p in t["parameters"].get("properties", {})) + "}"
        lines.append(f"- {t['name']} {example} — {t['description']}")
    return "\n".join(lines)


def actions_schema(specs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """JSON schema for constrained JSON replies (Ollama ``format``)."""
    return {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "enum": [t["name"] for t in specs or TOOL_SPECS]},
                        "args": {"type": "object"},
                    },
                    "required": ["tool", "args"],
                },
            }
        },
        "required": ["actions"],
    }
