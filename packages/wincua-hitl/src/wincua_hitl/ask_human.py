"""ask_human planner tool / HITL transport."""

from __future__ import annotations

from typing import Any

from wincua_hitl.local_ui import LocalUI

_default_ui: LocalUI | None = None


def set_default_ui(ui: LocalUI) -> None:
    global _default_ui
    _default_ui = ui


async def ask_human(
    question: str,
    *,
    options: list[str] | None = None,
    screenshot: Any | None = None,
    risk: str = "info",  # info | confirm | credential
    timeout_s: float = 300,
    ui: LocalUI | None = None,
) -> str | None:
    """Ask the human via local HITL UI. Fail-closed on timeout (returns None)."""
    transport = ui or _default_ui or LocalUI()
    return await transport.prompt(
        question=question,
        options=options,
        screenshot=screenshot,
        risk=risk,
        timeout_s=timeout_s,
    )
