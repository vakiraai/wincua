"""observe() façade over the active backend."""

from __future__ import annotations

from typing import Sequence

from wincua_driver.backend import get_backend
from wincua_driver.types import Observation


async def observe(
    target: str = "foreground",
    include: Sequence[str] | None = None,
) -> Observation:
    """Capture UIA / screenshot / app-pack state for a target window.

    Stub: delegates to the active backend (default NullBackend).
    """
    if include is None:
        include = ("uia", "screenshot")
    return await get_backend().observe(target, include)
