"""Null backend — no desktop I/O; safe for CI / non-Windows imports."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_driver.types import (
    Action,
    ActionResult,
    Observation,
    StructuredError,
)


class NullBackend:
    name = "null"

    def __init__(self) -> None:
        self.allow: list[str] = []

    def set_allow(self, allow: Sequence[str]) -> None:
        self.allow = list(allow)

    async def list_windows(self) -> list[dict[str, Any]]:
        return []

    async def observe(self, target: str, include: Sequence[str]) -> Observation:
        return Observation(
            errors=[
                StructuredError(
                    code="backend_null",
                    message=(
                        f"NullBackend: observe({target!r}) is a no-op. "
                        "Use MockBackend in tests or a Windows backend on host."
                    ),
                    details={"target": target, "include": list(include)},
                )
            ]
        )

    async def act(self, action: Action, *, dry_run: bool = False) -> ActionResult:
        return ActionResult(
            ok=True if dry_run else False,
            mode_used="dry-run" if dry_run else "null",
            error=None
            if dry_run
            else StructuredError(
                code="backend_null",
                message=f"NullBackend: act({type(action).__name__}) is a no-op.",
            ),
        )
