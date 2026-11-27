"""Mock backend — deterministic fake tree for unit tests."""

from __future__ import annotations

from typing import Sequence

from wincua_driver.types import (
    Action,
    ActionResult,
    Click,
    ElementRef,
    ImageRef,
    Invoke,
    Observation,
    Type,
)


class MockBackend:
    name = "mock"

    def __init__(self) -> None:
        self.actions: list[Action] = []

    async def observe(self, target: str, include: Sequence[str]) -> Observation:
        uia: list[ElementRef] = []
        screenshot = None
        app = None
        if "uia" in include:
            uia = [
                ElementRef(element_id="e1", role="Window", name=target or "Mock"),
                ElementRef(element_id="e2", role="Button", name="One", patterns=("Invoke",)),
                ElementRef(element_id="e3", role="Button", name="Equals", patterns=("Invoke",)),
            ]
        if "screenshot" in include:
            screenshot = ImageRef(path=None, data=b"", capture_quality="full")
        if "app" in include:
            app = {"display": "0", "target": target}
        return Observation(uia=uia, screenshot=screenshot, app=app)

    async def act(self, action: Action, *, dry_run: bool = False) -> ActionResult:
        self.actions.append(action)
        mode = "dry-run" if dry_run else "mock"
        if isinstance(action, (Click, Invoke, Type)):
            return ActionResult(ok=True, mode_used=mode)
        return ActionResult(ok=True, mode_used=mode)
