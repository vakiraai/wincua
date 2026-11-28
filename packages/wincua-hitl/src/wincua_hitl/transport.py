"""Transport base class plus the ``off`` and legacy-adapter transports."""

from __future__ import annotations

import re
import sys
from typing import Any, Sequence

from wincua_hitl.commands import parse_command
from wincua_hitl.types import (
    _CLICKABLE_ROLES,
    ALLOWED_RESPONSES,
    HumanRequest,
    HumanResponse,
    describe_action,
    observation_to_dict,
    screenshot_bytes,
    suggest_actions,
)

HISTORY_LIMIT = 50


class HitlTransport:
    """One channel for missing-model planning, questions and risky confirms.

    Subclasses implement ``_exchange``; everything else is shared. A ``None``
    response means timeout / unavailable, which callers treat as fail-closed.
    """

    name = "base"

    def __init__(self, *, timeout_s: float = 300.0, confirm_risky_actions: bool = True) -> None:
        self.timeout_s = float(timeout_s)
        self.confirm_risky_actions = confirm_risky_actions
        self.history: list[dict[str, Any]] = []

    async def start(self) -> str | None:
        return None

    async def stop(self) -> None:
        return None

    async def _exchange(self, req: HumanRequest, timeout_s: float) -> HumanResponse | None:
        raise NotImplementedError

    async def request(self, req: HumanRequest) -> HumanResponse | None:
        timeout = float(req.timeout_s or self.timeout_s)
        req.timeout_s = timeout
        resp = await self._exchange(req, timeout)
        if resp is not None:
            resp = normalize_response(req, resp)
        self._record(req, resp)
        return resp

    def _record(self, req: HumanRequest, resp: HumanResponse | None) -> None:
        text = resp.text if resp else None
        if req.risk == "credential" and text:
            text = "<redacted>"
        self.history.append(
            {
                "request_id": req.id,
                "kind": req.kind,
                "question": req.question,
                "action": req.action,
                "response": resp.kind if resp else "timeout",
                "text": text,
                "chosen": resp.action if resp else None,
                "at": resp.at if resp else None,
            }
        )
        del self.history[:-HISTORY_LIMIT]

    async def prompt(
        self,
        *,
        question: str,
        options: Sequence[str] | None = None,
        screenshot: Any | None = None,
        risk: str = "info",
        timeout_s: float | None = None,
        observation: Any | None = None,
        task: str | None = None,
    ) -> str | None:
        resp = await self.request(
            HumanRequest(
                kind="question",
                question=question,
                options=list(options or []),
                risk=risk,
                task=task,
                observation=observation_to_dict(observation),
                screenshot=screenshot_bytes(screenshot)
                or screenshot_bytes(getattr(observation, "screenshot", None)),
                timeout_s=timeout_s,
            )
        )
        if resp is None or resp.kind != "answer":
            return None
        return resp.text

    async def confirm(
        self,
        *,
        action: Any,
        reason: str | None = None,
        question: str | None = None,
        screenshot: Any | None = None,
        observation: Any | None = None,
        timeout_s: float | None = None,
        task: str | None = None,
    ) -> HumanResponse | None:
        proposed = describe_action(action)
        return await self.request(
            HumanRequest(
                kind="confirm",
                question=question or f"Allow the agent to run {proposed['tool']}?",
                risk="confirm",
                task=task,
                action=proposed,
                reason=reason,
                observation=observation_to_dict(observation),
                screenshot=screenshot_bytes(screenshot)
                or screenshot_bytes(getattr(observation, "screenshot", None)),
                timeout_s=timeout_s,
            )
        )

    async def next_step(
        self,
        *,
        task: str | None,
        observation: Any | None = None,
        screenshot: Any | None = None,
        question: str | None = None,
        timeout_s: float | None = None,
    ) -> HumanResponse | None:
        return await self.request(
            HumanRequest(
                kind="next_step",
                question=question or "No model is planning. Choose the next action.",
                task=task,
                observation=observation_to_dict(observation),
                screenshot=screenshot_bytes(screenshot)
                or screenshot_bytes(getattr(observation, "screenshot", None)),
                suggestions=suggest_actions(observation),
                timeout_s=timeout_s,
            )
        )

    async def takeover(
        self,
        *,
        task: str | None,
        observation: Any | None = None,
        timeout_s: float | None = None,
    ) -> HumanResponse | None:
        return await self.request(
            HumanRequest(
                kind="takeover",
                question="You have control of the desktop. Resume when you are finished.",
                task=task,
                observation=observation_to_dict(observation),
                screenshot=screenshot_bytes(getattr(observation, "screenshot", None)),
                timeout_s=timeout_s,
            )
        )


def normalize_response(req: HumanRequest, resp: HumanResponse) -> HumanResponse:
    """Map free text to commands and reject response kinds the request can't take."""
    if req.kind == "next_step" and resp.kind == "instruction":
        resp = interpret_text(resp.text or "", req)
    if req.kind == "question" and resp.kind == "answer" and resp.text is None:
        resp = HumanResponse(kind="answer", text="")
    if resp.kind == "action":
        if not resp.action or not resp.action.get("tool"):
            raise ValueError("action response needs {'tool': ..., 'args': {...}}")
        resp.action = {"tool": str(resp.action["tool"]), "args": dict(resp.action.get("args") or {})}
        if resp.actions:
            resp.actions = [
                {"tool": str(a["tool"]), "args": dict(a.get("args") or {})} for a in resp.actions if a.get("tool")
            ]
    if resp.kind not in ALLOWED_RESPONSES[req.kind]:
        raise ValueError(f"{resp.kind!r} is not a valid response to a {req.kind!r} request")
    return resp


_CHAIN = re.compile(r"\s*[|;]\s*")


def interpret_text(text: str, req: HumanRequest) -> HumanResponse:
    """Free text on a next_step: number -> suggestion, keyword, command, else instruction.

    ``A | B`` or ``A; B`` chains actions (run in order, stop at the first
    failure). A line starting with ``type`` is never split.
    """
    t = text.strip()
    if _CHAIN.search(t) and not t.lower().startswith("type "):
        parts = [p for p in _CHAIN.split(t) if p]
        resps = [_interpret_one(p, req) for p in parts]
        bad = [p for p, r in zip(parts, resps) if r.kind != "action"]
        if bad:
            return HumanResponse(
                kind="instruction",
                text=t,
                note=f"Nothing was run: {bad[0]!r} in the chain is not a command or a listed element.",
            )
        actions = [r.action for r in resps if r.action]
        return HumanResponse(kind="action", action=actions[0], actions=actions, text=t)
    return _interpret_one(t, req)


def _interpret_one(t: str, req: HumanRequest) -> HumanResponse:
    word, _, rest = t.partition(" ")
    if word.isdigit() and req.suggestions:
        idx = int(word) - 1
        if 0 <= idx < len(req.suggestions):
            s = req.suggestions[idx]
            args = dict(s["args"])
            if s.get("needs_text"):
                if not rest:
                    return HumanResponse(kind="instruction", text=t)
                args["text"] = rest
            return HumanResponse(kind="action", action={"tool": s["tool"], "args": args})
    if word.lower() in ("done", "abort", "takeover"):
        return HumanResponse(kind=word.lower(), text=rest.strip() or None)  # type: ignore[arg-type]
    cmd = parse_command(t)
    if cmd is not None:
        eid = cmd["args"].get("element_id")
        if eid and cmd["tool"] in ("invoke", "click", "type"):
            cmd["args"]["element_id"] = resolve_element(eid, req) or eid
        return HumanResponse(kind="action", action=cmd, text=t)
    eid = resolve_element(t, req)
    if eid:
        return HumanResponse(kind="action", action={"tool": "invoke", "args": {"element_id": eid}}, text=t)
    return HumanResponse(kind="instruction", text=t)


_SNAPSHOT_SUFFIX = re.compile(r"_\d+$")


def resolve_element(token: str, req: HumanRequest) -> str | None:
    """Map what a person types to a stable element id from the observation.

    Matches, case-insensitively and in this order: an element id (stable or
    snapshot), an AutomationId (``num1Button``, ``auto:num1Button``, or a stale
    ``num1Button_123``), then an element name (``One``, ``clear entry``).
    """
    t = (token or "").strip()
    if not t:
        return None
    elements = (req.observation or {}).get("uia") or []
    low = t.lower()
    bare = low[5:] if low.startswith("auto:") else low
    unsuffixed = _SNAPSHOT_SUFFIX.sub("", bare)
    for el in elements:
        if low in (str(el.get("element_id", "")).lower(), str(el.get("snapshot_id", "")).lower()):
            return el["element_id"]
    for el in elements:
        eid = str(el.get("element_id", ""))
        auto = eid[5:].lower() if eid.startswith("auto:") else ""
        if auto and auto in (bare, unsuffixed):
            return eid
    by_name = [el for el in elements if str(el.get("name", "")).strip().lower() == low]
    if by_name:
        clickable = [el for el in by_name if str(el.get("role", "")).lower() in _CLICKABLE_ROLES]
        return (clickable or by_name)[0]["element_id"]
    return None


class OffUI(HitlTransport):
    """``mode = "off"``: never asks; every request fails closed."""

    name = "off"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._warned = False

    async def _exchange(self, req: HumanRequest, timeout_s: float) -> HumanResponse | None:
        if not self._warned:
            print("wincua-hitl: mode=off, failing closed on human requests", file=sys.stderr)
            self._warned = True
        return None


class PromptAdapter(HitlTransport):
    """Wrap an object that only has ``prompt(question=..., risk=...)``."""

    name = "adapter"

    def __init__(self, inner: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.inner = inner

    async def start(self) -> str | None:
        fn = getattr(self.inner, "start", None)
        return await fn() if fn else None

    async def stop(self) -> None:
        fn = getattr(self.inner, "stop", None)
        if fn:
            await fn()

    async def _exchange(self, req: HumanRequest, timeout_s: float) -> HumanResponse | None:
        options = list(req.options)
        if req.kind == "confirm":
            options = ["approve", "reject", "takeover"]
        elif req.kind == "takeover":
            options = ["resume", "done", "abort"]
        answer = await self.inner.prompt(question=req.question, risk=req.risk)
        if answer is None:
            return None
        low = str(answer).strip().lower()
        if req.kind == "question":
            return HumanResponse(kind="answer", text=str(answer))
        if req.kind == "next_step":
            return HumanResponse(kind="instruction", text=str(answer))
        if low in options:
            return HumanResponse(kind=low)  # type: ignore[arg-type]
        if req.kind == "confirm":
            return HumanResponse(kind="approve" if low in ("y", "yes", "ok") else "reject")
        return HumanResponse(kind="resume")
