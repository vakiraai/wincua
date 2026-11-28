"""Tiny command grammar so a human can drive the agent with no model.

    invoke <element_id>          click <element_id> | click <x> <y>
    type <text>                  type @<element_id> <text>
    key <keys>                   wait <seconds>
    launch <app>                 focus <title>
    observe [target]

``done``, ``abort`` and ``takeover`` are handled by the transports.
"""

from __future__ import annotations

import shlex
from typing import Any

COMMAND_HELP = (
    "invoke <id> | click <id> | click <x> <y> | type <text> | type @<id> <text> | "
    "key <keys> | wait <s> | launch <app> | focus <title> | observe"
)


def parse_command(text: str) -> dict[str, Any] | None:
    """Return ``{"tool", "args"}`` for a recognised command, else None."""
    line = (text or "").strip()
    if not line:
        return None
    verb, _, rest = line.partition(" ")
    verb = verb.lower()
    rest = rest.strip()

    if verb in ("invoke", "press") and rest:
        return {"tool": "invoke", "args": {"element_id": rest}}
    if verb == "click" and rest:
        parts = rest.split()
        if len(parts) == 2 and all(_is_int(p) for p in parts):
            return {"tool": "click", "args": {"x": int(parts[0]), "y": int(parts[1])}}
        return {"tool": "click", "args": {"element_id": rest}}
    if verb == "type" and rest:
        if rest.startswith("@"):
            eid, _, body = rest[1:].partition(" ")
            if eid:
                return {"tool": "type", "args": {"element_id": eid, "text": _unquote(body)}}
        return {"tool": "type", "args": {"text": _unquote(rest)}}
    if verb in ("key", "hotkey") and rest:
        return {"tool": "key", "args": {"keys": rest}}
    if verb == "wait":
        try:
            return {"tool": "wait", "args": {"seconds": float(rest or 1)}}
        except ValueError:
            return None
    if verb == "launch" and rest:
        return {"tool": "launch", "args": {"app": rest}}
    if verb == "focus" and rest:
        return {"tool": "focus", "args": {"title": rest}}
    if verb == "observe":
        return {"tool": "observe", "args": {"target": rest} if rest else {}}
    return None


def _is_int(s: str) -> bool:
    try:
        int(s)
    except ValueError:
        return False
    return True


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        try:
            return shlex.split(s)[0]
        except ValueError:
            return s[1:-1]
    return s
