"""Default risky-action policy: which planner actions need human approval."""

from __future__ import annotations

import re
from typing import Any

RISKY_KEYS = {
    "alt+f4",
    "ctrl+w",
    "ctrl+shift+w",
    "ctrl+q",
    "delete",
    "del",
    "shift+delete",
    "ctrl+d",
    "ctrl+s",
    "ctrl+shift+s",
    "ctrl+p",
    "ctrl+enter",
    "win+r",
    "win+l",
}

_RISKY_WORDS = re.compile(
    r"\b(delete|remove|erase|format|uninstall|send|submit|pay|purchase|buy|checkout|"
    r"transfer|confirm|sign ?out|log ?out|shut ?down|restart|close|discard|overwrite|"
    r"save)\b",
    re.IGNORECASE,
)


def risk_reason(tool: str, args: dict[str, Any] | None = None) -> str | None:
    """Why ``tool(args)`` needs confirmation, or None when it is low risk."""
    args = args or {}
    name = (tool or "").replace("-", "_").lower()
    if str(args.get("risk", "")).lower() in ("confirm", "credential"):
        return "planner marked this step as risky"
    if name in ("key", "hotkey"):
        keys = str(args.get("keys", "")).lower().replace(" ", "")
        if keys in RISKY_KEYS:
            return f"hotkey {keys!r} can close, delete or save"
    if name in ("type", "type_text") and args.get("submit"):
        return "typing with submit=True sends the form"
    if name == "semantic":
        return f"semantic app action {args.get('name')!r}"
    if name in ("invoke", "uia.invoke", "click"):
        label = " ".join(
            str(args.get(k) or "") for k in ("name", "element_id", "auto_id")
        )
        label = re.sub(r"([a-z])([A-Z])", r"\1 \2", label).replace("_", " ").replace(":", " ")
        m = _RISKY_WORDS.search(label)
        if m:
            return f"control looks destructive or final ({m.group(0)!r})"
    return None
