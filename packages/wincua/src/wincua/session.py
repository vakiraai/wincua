"""Session — allow-list, mode, recorder, HITL URL."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


Mode = Literal["foreground", "background", "dry-run"]


@dataclass
class Session:
    allow: list[str] = field(default_factory=list)
    mode: Mode = "foreground"
    recorder: bool = False
    hitl: Any = None
    provider: Any = None
    session_id: str | None = None
    recording_path: str | None = None
    _active: bool = False

    async def start(self) -> str:
        self.session_id = self.session_id or str(uuid.uuid4())
        self._active = True
        if self.hitl is not None and hasattr(self.hitl, "start"):
            await self.hitl.start()
        if self.recorder:
            self.recording_path = f"recordings/{self.session_id}.jsonl"
        return self.session_id

    async def end(self) -> str | None:
        self._active = False
        if self.hitl is not None and hasattr(self.hitl, "stop"):
            await self.hitl.stop()
        return self.recording_path
