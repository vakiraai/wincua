"""Helpers that keep a model planner on track: arg cleanup, short errors, loop detection."""

from __future__ import annotations

import json
import re
from typing import Any

ELEMENT_TOOLS = frozenset({"invoke", "uia.invoke", "type", "type_text", "click"})
_SNAPSHOT_SUFFIX = re.compile(r"_\d+$")
_DUMP_MARKERS = ("\nUIA tree dump", "\nVisible titles", "\nTraceback")


def short_error(message: str | None, limit: int = 300) -> str | None:
    """Model-facing error text: drop tree dumps / tracebacks, collapse whitespace, cap length."""
    if not message:
        return message
    head = message
    for marker in _DUMP_MARKERS:
        head = head.split(marker, 1)[0]
    head = " ".join(head.split())
    return head if len(head) <= limit else head[: limit - 1] + "…"


def _auto_id(el: Any) -> str:
    eid = getattr(el, "element_id", "") or ""
    return getattr(el, "auto_id", "") or (eid[5:] if eid.startswith("auto:") else "")


def normalize_model_args(
    tool: str, args: dict[str, Any], observation: Any, *, current_target: str | None
) -> dict[str, Any]:
    """Clean tool args written by a model before they reach the driver.

    * drops empty values and keys the tools do not take (``role``),
    * keeps ``target`` only if it names the observed window,
    * maps ``element_id`` to a stable ``auto:<AutomationId>`` when it is a bare
      AutomationId, a snapshot id (``clearButton_112``) or an element name.
    """
    out = {k: v for k, v in args.items() if v not in (None, "") and k not in ("role", "tool")}
    if "target" in out:
        from wincua_driver.backend.windows import match_window_title

        want = str(out["target"])
        observed = [str(current_target or "")]
        app = getattr(observation, "app", None) or {}
        observed.extend(str(app.get(k) or "") for k in ("title", "target", "page"))
        if not any(t and match_window_title(t, want) for t in observed if t):
            out.pop("target")
    if tool not in ELEMENT_TOOLS:
        return out

    uia = list(getattr(observation, "uia", None) or [])
    eid = str(out.get("element_id") or "")
    if not eid:
        if out.get("auto_id"):
            out["element_id"] = f"auto:{out['auto_id']}"
        elif out.get("name"):
            out["element_id"] = f"name:{out['name']}"
        return out

    if eid.startswith("auto:"):
        out.setdefault("auto_id", eid[5:])
        return out
    if eid.startswith("name:"):
        out.setdefault("name", eid[5:])
        return out

    match = next((el for el in uia if el.element_id == eid), None)
    if match is None:
        base = _SNAPSHOT_SUFFIX.sub("", eid)
        match = next((el for el in uia if _auto_id(el) in (eid, base)), None)
        if match is None:
            match = next((el for el in uia if (el.name or "").lower() == eid.lower()), None)
            if match is not None and not _auto_id(match):
                out["element_id"] = f"name:{match.name}"
                out["name"] = match.name
                return out
        if match is None:
            # Unknown here; let the driver deep-search it as an AutomationId.
            out["element_id"] = f"auto:{base}"
            out["auto_id"] = base
            return out
    auto = _auto_id(match)
    if auto:
        out["element_id"] = f"auto:{auto}"
        out["auto_id"] = auto
    return out


def action_signature(tool: str, args: dict[str, Any]) -> str:
    sig = {k: v for k, v in args.items() if k not in {"target", "name", "auto_id"} and not k.startswith("_")}
    return f"{tool}:{json.dumps(sig, sort_keys=True, default=str)}"


def observation_fingerprint(observation: Any) -> str:
    app = getattr(observation, "app", None) or {}
    errors = [getattr(e, "code", "") for e in getattr(observation, "errors", None) or []]
    return json.dumps([app, errors], sort_keys=True, default=str)


class LoopGuard:
    """Spots a planner going in circles.

    * the same action list for ``repeat_limit`` turns in a row. The whole list is
      compared, not single actions, so pressing one button twice within a turn
      (``1``, ``1`` for 11) is fine. The screen is ignored: a stuck planner may
      still change it (e.g. typing ``1`` again and again),
    * ``failure_limit`` failed tool calls in a row.
    """

    def __init__(self, *, repeat_limit: int = 3, failure_limit: int = 3) -> None:
        self.repeat_limit = repeat_limit
        self.failure_limit = failure_limit
        self.reset()

    def reset(self) -> None:
        self._last_turn: tuple[str, ...] | None = None
        self._last_fingerprint: str | None = None
        self._screen_changed = False
        self._repeats = 0
        self._failures = 0
        self._last_failed: str | None = None
        self.warning: str | None = None

    def record_action(self, signature: str, ok: bool) -> str | None:
        if ok:
            self._failures = 0
            return None
        self._failures += 1
        self._last_failed = signature
        if self._failures >= self.failure_limit:
            return f"{self._failures} failed actions in a row (last: {signature})"
        return None

    def end_turn(self, fingerprint: str, signatures: list[str]) -> str | None:
        self.warning = None
        if not signatures:
            return None
        key = tuple(signatures)
        if key == self._last_turn:
            self._repeats += 1
            self._screen_changed |= fingerprint != self._last_fingerprint
        else:
            self._repeats, self._screen_changed = 1, False
        self._last_turn, self._last_fingerprint = key, fingerprint
        if self._repeats == self.repeat_limit - 1:
            # A temperature-0 model sent the same prompt answers the same way: change the
            # prompt once (see Agent) before the next repeat escalates.
            shown = ", ".join(signatures[:4]) + (", …" if len(signatures) > 4 else "")
            self.warning = (
                f"You sent the same actions {self._repeats} turns in a row ({shown}) and they already ran. "
                "Do not send them again: send the next step of the task, or done if its result is "
                "already visible. Repeating them once more stops the run."
            )
        if self._repeats >= self.repeat_limit:
            screen = "" if self._screen_changed else " without changing the screen"
            shown = ", ".join(signatures[:4]) + (", …" if len(signatures) > 4 else "")
            return f"same actions repeated {self._repeats} turns in a row{screen} ({shown})"
        return None
