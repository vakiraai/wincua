"""Request / response shapes shared by every HITL transport."""

from __future__ import annotations

import dataclasses
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

RequestKind = Literal["question", "confirm", "next_step", "takeover"]
ResponseKind = Literal[
    "answer",  # question: free text or chosen option
    "approve",  # confirm
    "reject",  # confirm
    "action",  # next_step: {"tool": ..., "args": {...}}
    "instruction",  # next_step: free text that is not a known command
    "takeover",  # confirm / next_step: human drives the desktop
    "resume",  # takeover: human hands control back
    "done",
    "abort",
]

ALLOWED_RESPONSES: dict[str, frozenset[str]] = {
    "question": frozenset({"answer", "abort"}),
    "confirm": frozenset({"approve", "reject", "takeover"}),
    "next_step": frozenset({"action", "instruction", "takeover", "done", "abort"}),
    "takeover": frozenset({"resume", "done", "abort"}),
}

MAX_ELEMENTS = 300


@dataclass
class HumanRequest:
    kind: RequestKind
    question: str = ""
    options: list[str] = field(default_factory=list)
    risk: str = "info"  # info | confirm | credential
    task: str | None = None
    action: dict[str, Any] | None = None  # proposed action (confirm)
    reason: str | None = None  # why it needs confirmation
    observation: dict[str, Any] | None = None
    screenshot: bytes | None = None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    timeout_s: float | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)

    def public_dict(self) -> dict[str, Any]:
        """JSON-safe view for the UI (screenshot bytes are served separately)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "question": self.question,
            "options": list(self.options),
            "risk": self.risk,
            "task": self.task,
            "action": self.action,
            "reason": self.reason,
            "observation": self.observation,
            "has_screenshot": bool(self.screenshot),
            "suggestions": self.suggestions,
            "timeout_s": self.timeout_s,
            "created_at": self.created_at,
            "allowed": sorted(ALLOWED_RESPONSES[self.kind]),
        }


@dataclass
class HumanResponse:
    kind: ResponseKind
    text: str | None = None
    action: dict[str, Any] | None = None
    at: float = field(default_factory=time.time)
    # Chained commands ("A | B", "A; B"): every action in order; ``action`` is the first.
    actions: list[dict[str, Any]] | None = None
    # Why free text was not turned into an action, shown on the next prompt.
    note: str | None = None

    @property
    def approved(self) -> bool:
        return self.kind == "approve"


def screenshot_bytes(shot: Any) -> bytes | None:
    """Accept ``ImageRef``, raw bytes or a file path; return image bytes or None."""
    if shot is None:
        return None
    if isinstance(shot, (bytes, bytearray)):
        return bytes(shot) or None
    if isinstance(shot, (str, Path)):
        p = Path(shot)
        return p.read_bytes() if p.is_file() else None
    data = getattr(shot, "data", None)
    if data:
        return bytes(data)
    path = getattr(shot, "path", None)
    if path:
        return screenshot_bytes(path)
    return None


def image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def observation_to_dict(obs: Any) -> dict[str, Any] | None:
    """Duck-typed ``wincua_driver.Observation`` -> JSON-safe dict."""
    if obs is None:
        return None
    if isinstance(obs, dict):
        return obs
    elements = []
    for el in list(getattr(obs, "uia", None) or [])[:MAX_ELEMENTS]:
        snapshot_id = getattr(el, "element_id", "") or ""
        auto_id = getattr(el, "auto_id", "") or ""
        elements.append(
            {
                # auto:<AutomationId> survives re-observation; snapshot ids
                # like num1Button_123 change every turn.
                "element_id": f"auto:{auto_id}" if auto_id else snapshot_id,
                "snapshot_id": snapshot_id,
                "role": getattr(el, "role", ""),
                "name": getattr(el, "name", ""),
                "bounds": list(el.bounds) if getattr(el, "bounds", None) else None,
                "patterns": list(getattr(el, "patterns", ()) or ()),
            }
        )
    shot = getattr(obs, "screenshot", None)
    return {
        "app": _json_safe(getattr(obs, "app", None)),
        "errors": [
            {"code": getattr(e, "code", ""), "message": getattr(e, "message", str(e))}
            for e in (getattr(obs, "errors", None) or [])
        ],
        "uia": elements,
        "uia_total": len(getattr(obs, "uia", None) or []),
        "capture_quality": getattr(shot, "capture_quality", None) if shot is not None else None,
    }


_CLICKABLE_ROLES = {
    "button",
    "menuitem",
    "listitem",
    "hyperlink",
    "tabitem",
    "checkbox",
    "radiobutton",
    "splitbutton",
}


def suggest_actions(obs: Any, *, limit: int = 60) -> list[dict[str, Any]]:
    """Next-action choices derived from the observation's UIA elements."""
    data = observation_to_dict(obs) or {}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for el in data.get("uia", []):
        role = str(el.get("role") or "")
        name = str(el.get("name") or "")
        eid = str(el.get("element_id") or "")
        if not eid:
            continue
        key = (role.lower(), name)
        if role.lower() == "edit":
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "label": f"Type into {name or eid}",
                    "tool": "type",
                    "args": {"element_id": eid, "text": ""},
                    "needs_text": True,
                }
            )
        elif "Invoke" in el.get("patterns", []) or role.lower() in _CLICKABLE_ROLES:
            if not name or key in seen:
                continue
            seen.add(key)
            out.append({"label": f"{name}", "tool": "invoke", "args": {"element_id": eid}})
        if len(out) >= limit:
            break
    return out


def describe_action(action: Any) -> dict[str, Any]:
    """Normalize a PlanStep, driver Action dataclass, or dict to ``{"tool", "args"}``."""
    if action is None:
        return {"tool": "unknown", "args": {}}
    if isinstance(action, dict):
        if "tool" in action:
            return {"tool": str(action["tool"]), "args": _json_safe(action.get("args") or {})}
        if "name" in action:
            return {"tool": str(action["name"]), "args": _json_safe(action.get("args") or {})}
        return {"tool": "unknown", "args": _json_safe(action)}
    name = getattr(action, "name", None)
    kind = getattr(action, "kind", None)
    if kind is not None and hasattr(action, "args"):  # PlanStep
        return {"tool": str(name or kind), "args": _json_safe(getattr(action, "args") or {})}
    if dataclasses.is_dataclass(action):
        fields = _json_safe(dataclasses.asdict(action))
        tool = type(action).__name__.lower()
        if tool == "semantic":
            return {"tool": str(fields.get("name")), "args": fields.get("args") or {}}
        return {"tool": tool, "args": {k: v for k, v in fields.items() if v is not None}}
    return {"tool": str(action), "args": {}}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return str(value)
