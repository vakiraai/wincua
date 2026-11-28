"""Human-in-the-loop hook used by ``ask_human`` and risky ``act`` confirmations."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Protocol

APPROVE_ANSWERS = frozenset({"approve", "approved", "allow", "yes", "y", "ok"})


class HitlHook(Protocol):
    name: str

    async def ask(
        self,
        question: str,
        *,
        options: list[str] | None = None,
        risk: str = "info",
        screenshot: Any | None = None,
        timeout_s: float = 300,
    ) -> str | None: ...


class WincuaHitlHook:
    """Delegates to ``wincua_hitl`` (the ``[hitl]`` transport; local UI by default).

    ``mode = "cli"`` is replaced by the browser UI: on stdio, stdin is the MCP channel.
    """

    name = "wincua_hitl"

    def __init__(self, transport: Any | None = None) -> None:
        import wincua_hitl

        self._hitl = wincua_hitl
        self._transport = transport

    @property
    def transport(self) -> Any:
        if self._transport is None:
            transport = self._hitl.from_settings()
            if isinstance(transport, self._hitl.CliUI):
                print(
                    "wincua-mcp: [hitl] mode = \"cli\" would read the MCP stdin; using the local UI instead.",
                    file=sys.stderr,
                )
                transport = self._hitl.from_settings(mode="ui")
            self._transport = transport
        return self._transport

    async def ask(
        self,
        question: str,
        *,
        options: list[str] | None = None,
        risk: str = "info",
        screenshot: Any | None = None,
        timeout_s: float = 300,
    ) -> str | None:
        return await self._hitl.ask_human(
            question,
            options=options,
            screenshot=screenshot,
            risk=risk,
            timeout_s=timeout_s,
            ui=self.transport,
        )

    async def confirm(
        self,
        summary: str,
        *,
        action: Any | None = None,
        timeout_s: float = 300,
    ) -> tuple[bool, str | None]:
        """Approve/Reject card for ``action``; returns (approved, response kind or None)."""
        resp = await self.transport.confirm(
            action=action if action is not None else {"tool": "act", "args": {}},
            reason=summary,
            question=f"An MCP client wants to run a risky desktop action: {summary}. Approve?",
            timeout_s=timeout_s,
        )
        if resp is None:
            return False, None
        return resp.kind == "approve", resp.kind


class StubHitlHook:
    """No human channel available: every question goes unanswered (fail-closed)."""

    name = "stub"

    async def ask(
        self,
        question: str,
        *,
        options: list[str] | None = None,
        risk: str = "info",
        screenshot: Any | None = None,
        timeout_s: float = 300,
    ) -> str | None:
        return None


def default_hitl_hook() -> HitlHook:
    try:
        return WincuaHitlHook()
    except ImportError:
        return StubHitlHook()


@dataclass(frozen=True)
class Confirmation:
    approved: bool
    answer: str | None
    hook: str


async def confirm_action(
    hook: HitlHook, summary: str, *, timeout_s: float, action: Any | None = None
) -> Confirmation:
    confirm = getattr(hook, "confirm", None)
    if confirm is not None:
        approved, answer = await confirm(summary, action=action, timeout_s=timeout_s)
        return Confirmation(approved=approved, answer=answer, hook=hook.name)
    answer = await hook.ask(
        f"An MCP client wants to run a risky desktop action: {summary}. Approve?",
        options=["approve", "deny"],
        risk="confirm",
        timeout_s=timeout_s,
    )
    approved = answer is not None and answer.strip().lower() in APPROVE_ANSWERS
    return Confirmation(approved=approved, answer=answer, hook=hook.name)
