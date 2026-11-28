"""Shared prompt pieces: system prompt, observation text, history grouping."""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from typing import Any, Sequence

SYSTEM_PROMPT = """\
You are wincua, a computer-use agent operating real Windows desktop apps through \
UI Automation (UIA) tools.

Each turn you receive the current observation of the target window: app state, \
errors, a list of Elements (id role "name") and, when available, a screenshot. \
Act with the provided tools.

Rules:
- Use invoke/type with an element_id copied exactly from the Elements list; avoid x/y clicks.
- You may take several actions per turn when you know the sequence (e.g. pressing digits): \
make one separate tool call per action, in order. Never put a tool call inside another \
call's arguments.
- Do not repeat an action that already succeeded unless the task needs it again; \
check the history and move to the next step.
- If an action fails, read the error and try a different element or approach.
- Call done(message) only when the task is complete and the result is visible.
- Call ask_human(question) if the task is ambiguous, risky or you are stuck; \
call abort(reason) if it is impossible.
- Never act on apps outside the task."""

MAX_ELEMENTS = 80
MAX_HISTORY_TURNS = 12


def observation_text(observation: Any, *, max_elements: int = MAX_ELEMENTS) -> str:
    if observation is None:
        return "No observation available."
    lines: list[str] = []
    app = getattr(observation, "app", None)
    if app:
        target = app.get("target") or app.get("title")
        if target:
            lines.append(f"Target window: {target}")
        rest = {k: v for k, v in app.items() if k not in {"target"} and v not in (None, "")}
        if rest:
            lines.append(f"App state: {json.dumps(rest, ensure_ascii=False, default=str)}")
    errors = getattr(observation, "errors", None) or []
    for err in errors:
        lines.append(f"Error [{getattr(err, 'code', '?')}]: {getattr(err, 'message', err)}")
    rows = compact_elements(getattr(observation, "uia", None) or [], max_elements=max_elements)
    if rows:
        lines.append("Elements (pass the id as element_id):")
        lines.extend(f'{eid} {role} "{name}"' if name else f"{eid} {role}" for eid, role, name in rows)
    else:
        lines.append("UI elements: none (window missing or no UIA tree).")
    return "\n".join(lines)


def auto_id_of(el: Any) -> str:
    auto = getattr(el, "auto_id", "") or ""
    eid = getattr(el, "element_id", "") or ""
    return auto or (eid[5:] if eid.startswith("auto:") else "")


def element_key(el: Any) -> str:
    """Stable id to show the model: ``auto:<AutomationId>``, else ``name:<Name>``."""
    auto = auto_id_of(el)
    if auto:
        return f"auto:{auto}"
    eid = getattr(el, "element_id", "") or ""
    if eid.startswith("name:") or not getattr(el, "name", ""):
        return eid
    return f"name:{el.name}"


def compact_elements(uia: Sequence[Any], *, max_elements: int = MAX_ELEMENTS) -> list[tuple[str, str, str]]:
    """(id, role, name) rows: named or AutomationId'd elements only, one row per id/name."""
    with_auto = {el.name for el in uia if auto_id_of(el) and el.name}
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for el in uia:
        if not auto_id_of(el) and (not el.name or el.name in with_auto):
            continue
        key = element_key(el)
        if key in seen:
            continue
        seen.add(key)
        rows.append((key, el.role or "", (el.name or "")[:80]))
        if len(rows) >= max_elements:
            break
    return rows


def screenshot_png(observation: Any) -> bytes | None:
    shot = getattr(observation, "screenshot", None)
    data = getattr(shot, "data", None) if shot is not None else None
    if not data or not isinstance(data, (bytes, bytearray)):
        return None
    return bytes(data)


def screenshot_b64(observation: Any) -> str | None:
    data = screenshot_png(observation)
    return base64.b64encode(data).decode("ascii") if data else None


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return None


def window_origin(observation: Any) -> tuple[int, int]:
    """Screen offset of the window screenshot (first UIA element is the window)."""
    uia = getattr(observation, "uia", None) or []
    if uia and getattr(uia[0], "bounds", None):
        left, top = uia[0].bounds[0], uia[0].bounds[1]
        return int(left), int(top)
    return 0, 0


def user_turn_text(task: str, observation: Any, *, first: bool, notes: Sequence[str] = ()) -> str:
    obs = observation_text(observation)
    head = f"Task: {task}" if first else ""
    head = "\n".join([head, *notes]).strip()
    tail = "" if first else f"\n\nContinue the task: {task}"
    prefix = f"{head}\n\n" if head else ""
    return f"{prefix}Current observation:\n{obs}{tail}"


@dataclass
class ToolCallRecord:
    call_id: str
    name: str
    args: dict[str, Any]
    result: str


@dataclass
class Turn:
    index: int
    text: str | None = None
    calls: list[ToolCallRecord] = field(default_factory=list)
    # Notes (human instructions, rejected actions…) with no tool call to attach to.
    notes: list[str] = field(default_factory=list)


# History entries that are planner tool calls; other kinds become notes.
CALL_KINDS = frozenset({"tool", "ask_human", "done", "abort"})


def result_text(entry: dict[str, Any]) -> str:
    if entry.get("kind") == "ask_human":
        answer = (entry.get("result") or {}).get("answer")
        return f"Human answered: {answer}" if answer is not None else "No human answer."
    res = entry.get("result")
    if res is None:
        return "not executed"
    parts = ["ok" if res.get("ok", True) else "FAILED"]
    if res.get("message") or res.get("code"):
        code = f"[{res['code']}] " if res.get("code") else ""
        parts.append(f"{code}{res.get('message') or ''}".strip())
    if res.get("hint"):
        parts.append(f"hint: {res['hint']}")
    for key in ("display", "info"):
        if res.get(key):
            parts.append(f"{key}={json.dumps(res[key], ensure_ascii=False, default=str)[:300]}")
    return ": ".join(parts[:2]) + ("; " + "; ".join(parts[2:]) if len(parts) > 2 else "")


def group_history(
    history: Sequence[dict[str, Any]] | None, *, max_turns: int = MAX_HISTORY_TURNS
) -> list[Turn]:
    """Group the Agent's flat history into model turns (assistant calls + results)."""
    turns: dict[int, Turn] = {}
    order: list[int] = []
    last_idx = 0
    for entry in history or []:
        idx = int(entry["turn"]) if entry.get("turn") is not None else last_idx
        last_idx = idx
        if idx not in turns:
            turns[idx] = Turn(index=idx)
            order.append(idx)
        turn = turns[idx]
        kind = entry.get("kind")
        if kind == "model_text":
            turn.text = entry.get("text") or ""
            continue
        if kind not in CALL_KINDS or not entry.get("name"):
            note = history_note(entry)
            if turn.calls:
                turn.calls[-1].result = f"{turn.calls[-1].result}\n{note}"
            else:
                turn.notes.append(note)
            continue
        name = entry["name"]
        call_id = entry.get("call_id") or f"call_{idx}_{len(turn.calls)}"
        turn.calls.append(
            ToolCallRecord(
                call_id=str(call_id),
                name=str(name),
                args=dict(entry.get("args") or {}),
                result=result_text(entry),
            )
        )
    return [turns[i] for i in order][-max_turns:]


def history_note(entry: dict[str, Any]) -> str:
    kind = entry.get("kind")
    name = entry.get("name")
    if kind == "escalation":
        return str(entry.get("text"))
    if kind == "human_instruction":
        return f"Human instruction: {entry.get('text')}"
    if kind == "rejected":
        return f"The human rejected {name}({json.dumps(clean_args(entry.get('args') or {}))}); do not retry it as is."
    if kind == "tool_failed":
        return f"{name} failed: {entry.get('message')}"
    detail = {k: v for k, v in entry.items() if k not in {"turn", "call_id"}}
    return f"Note: {json.dumps(detail, ensure_ascii=False, default=str)[:300]}"


def orphan_notes(turns: Sequence[Turn]) -> list[str]:
    return [n for t in turns for n in t.notes]


NUDGE = "Reply with a tool call (or done / abort / ask_human)."


def json_turn_messages(turn: Turn) -> list[dict[str, Any]]:
    """A past turn in JSON-reply mode: the reply as the model should write it, then its results."""
    actions = [{"tool": c.name, "args": clean_args(c.args)} for c in turn.calls]
    results = "\n".join(f"{i}. {c.name}: {c.result}" for i, c in enumerate(turn.calls, 1))
    return [
        {"role": "assistant", "content": json.dumps({"actions": actions}, ensure_ascii=False)},
        {"role": "user", "content": f"Results:\n{results}"},
    ]


def clean_args(args: dict[str, Any]) -> dict[str, Any]:
    """Drop provider-private keys (``_cu`` etc.) before echoing args to a model."""
    return {k: v for k, v in args.items() if not k.startswith("_")}
