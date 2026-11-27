"""act() façade over the active backend."""

from __future__ import annotations

from wincua_driver.backend import get_backend
from wincua_driver.types import Action, ActionResult


async def act(action: Action, *, dry_run: bool = False) -> ActionResult:
    """Dispatch a desktop action (Click / Type / Invoke / Key / Semantic).

    Stub: delegates to the active backend (default NullBackend).
    """
    return await get_backend().act(action, dry_run=dry_run)
