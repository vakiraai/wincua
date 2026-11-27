"""LocalUI stub — FastAPI+static server will bind here in Phase 0/1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LocalUI:
    """HITL transport config. Same channel for offline planner + risk confirms."""

    host: str = "127.0.0.1"
    port: int = 8765
    _started: bool = False

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def start(self) -> str:
        """Start local UI server (stub — no listen yet)."""
        self._started = True
        return self.url

    async def stop(self) -> None:
        self._started = False

    async def prompt(
        self,
        *,
        question: str,
        options: list[str] | None = None,
        screenshot: Any | None = None,
        risk: str = "info",
        timeout_s: float = 300,
    ) -> str | None:
        """Block for human answer. Stub returns None (fail-closed)."""
        # Real implementation: show question/options/screenshot; await response.
        _ = (options, screenshot, risk, timeout_s)
        if not self._started:
            await self.start()
        return None
