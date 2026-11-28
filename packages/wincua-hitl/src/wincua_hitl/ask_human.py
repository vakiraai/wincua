"""Stable HITL API used by the agent loop and the MCP server."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_hitl.transport import HitlTransport

_default_ui: HitlTransport | None = None


def set_default_ui(ui: Any) -> None:
    """Set the process-wide transport (a transport, a mode name, or None to reset)."""
    global _default_ui
    from wincua_hitl.factory import make_transport

    _default_ui = make_transport(ui)


def get_default_ui() -> HitlTransport:
    """Process-wide transport, built once from ``[hitl]`` config on first use."""
    global _default_ui
    if _default_ui is None:
        from wincua_hitl.factory import from_settings

        _default_ui = from_settings()
    return _default_ui


def _resolve(ui: Any) -> HitlTransport:
    if ui is None:
        return get_default_ui()
    from wincua_hitl.factory import make_transport

    transport = make_transport(ui)
    assert transport is not None
    return transport


async def ask_human(
    question: str,
    *,
    options: Sequence[str] | None = None,
    screenshot: Any | None = None,
    risk: str = "info",  # info | confirm | credential
    timeout_s: float | None = None,
    ui: Any | None = None,
    observation: Any | None = None,
    task: str | None = None,
) -> str | None:
    """Ask the human a question. Returns the answer, or None on timeout/abort (fail-closed).

    ``timeout_s=None`` uses ``[hitl] timeout_s`` (default 300).
    """
    return await _resolve(ui).prompt(
        question=question,
        options=options,
        screenshot=screenshot,
        risk=risk,
        timeout_s=timeout_s,
        observation=observation,
        task=task,
    )


async def confirm_action(
    action: Any,
    *,
    reason: str | None = None,
    question: str | None = None,
    screenshot: Any | None = None,
    observation: Any | None = None,
    timeout_s: float | None = None,
    ui: Any | None = None,
    task: str | None = None,
) -> bool:
    """Ask the human to approve a risky action. True only on explicit approval.

    ``action`` may be a ``{"tool", "args"}`` dict, a ``PlanStep`` or a driver
    action dataclass. Reject, take-over, timeout and ``mode = "off"`` all
    return False. Use ``transport.confirm()`` to tell those apart.
    """
    resp = await _resolve(ui).confirm(
        action=action,
        reason=reason,
        question=question,
        screenshot=screenshot,
        observation=observation,
        timeout_s=timeout_s,
        task=task,
    )
    return bool(resp and resp.approved)
