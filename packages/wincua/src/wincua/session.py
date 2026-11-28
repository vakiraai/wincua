"""Session — allow-list, mode, recorder, HITL URL."""

from __future__ import annotations

import dataclasses
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
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
    record_error: str | None = None
    _active: bool = False
    _t0: float = 0.0

    async def start(self) -> str:
        self.session_id = self.session_id or str(uuid.uuid4())
        self._active = True
        self._t0 = time.monotonic()
        if self.hitl is not None and hasattr(self.hitl, "start"):
            await self.hitl.start()
        if self.recorder:
            self.recording_path = f"recordings/{self.session_id}.jsonl"
            try:
                Path(self.recording_path).parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._recording_failed(exc)
        return self.session_id

    def record(self, event: dict[str, Any]) -> None:
        """Append one event as a JSON line, written immediately so a crashed run keeps its turns.

        Recording must never break a run: on a write error the recording is
        dropped and ``recording_path`` becomes None.
        """
        if not self.recording_path:
            return
        line = {"t": round(time.monotonic() - self._t0, 3), "session": self.session_id, **event}
        try:
            text = json.dumps(line, default=_jsonable, ensure_ascii=False)
            with open(self.recording_path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except (OSError, TypeError, ValueError) as exc:
            self._recording_failed(exc)

    def _recording_failed(self, exc: Exception) -> None:
        self.record_error = f"{type(exc).__name__}: {exc}"
        self.recording_path = None

    async def end(self) -> str | None:
        self._active = False
        if self.hitl is not None and hasattr(self.hitl, "stop"):
            await self.hitl.stop()
        return self.recording_path


def _jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        try:
            return dataclasses.asdict(obj)
        except Exception:
            return repr(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        return f"<{len(obj)} bytes>"
    return repr(obj)
