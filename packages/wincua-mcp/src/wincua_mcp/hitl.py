"""Human-in-the-loop hook used by ``ask_human`` and risky ``act`` confirmations."""

from __future__ import annotations

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
    """Delegates to ``wincua_hitl.ask_human`` (local HITL UI)."""

    name = "wincua_hitl"

    def __init__(self) -> None:
        from wincua_hitl import ask_human

        self._ask_human = ask_human

    async def ask(
        self,
        question: str,
        *,
        options: list[str] | None = None,
        risk: str = "info",
        screenshot: Any | None = None,
        timeout_s: float = 300,
    ) -> str | None:
        return await self._ask_human(
            question,
            options=options,
            screenshot=screenshot,
            risk=risk,
            timeout_s=timeout_s,
        )


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


async def confirm_action(hook: HitlHook, summary: str, *, timeout_s: float) -> Confirmation:
    answer = await hook.ask(
        f"An MCP client wants to run a risky desktop action: {summary}. Approve?",
        options=["approve", "deny"],
        risk="confirm",
        timeout_s=timeout_s,
    )
    approved = answer is not None and answer.strip().lower() in APPROVE_ANSWERS
    return Confirmation(approved=approved, answer=answer, hook=hook.name)
