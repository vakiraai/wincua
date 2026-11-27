"""Driver backend protocol."""

from __future__ import annotations

from typing import Protocol, Sequence

from wincua_driver.types import Action, ActionResult, Observation


class DriverBackend(Protocol):
    name: str

    async def observe(
        self,
        target: str,
        include: Sequence[str],
    ) -> Observation: ...

    async def act(self, action: Action, *, dry_run: bool = False) -> ActionResult: ...
