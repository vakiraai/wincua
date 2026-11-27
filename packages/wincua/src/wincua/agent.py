"""Agent loop stub — plan → observe/act/ask_human until done."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from wincua.session import Session


Mode = Literal["foreground", "background", "dry-run"]


@dataclass
class AgentResult:
    ok: bool
    steps: list[dict[str, Any]] = field(default_factory=list)
    recording_path: str | None = None
    message: str | None = None


@dataclass
class Agent:
    task: str
    provider: Any = None
    hitl: Any = None
    allow: Sequence[str] = ()
    mode: Mode = "foreground"
    recorder: bool = False

    def __post_init__(self) -> None:
        if self.provider is None:
            from wincua_providers import NullProvider

            self.provider = NullProvider()

    async def run(self) -> AgentResult:
        """Run the agent loop (stub).

        On host Windows this will: observe → provider.plan → act / ask_human
        until done. Stub returns a structured placeholder without desktop I/O.
        """
        session = Session(
            allow=list(self.allow),
            mode=self.mode,
            recorder=self.recorder,
            hitl=self.hitl,
            provider=self.provider,
        )
        await session.start()
        steps: list[dict[str, Any]] = [
            {"kind": "session_start", "session_id": session.session_id},
            {"kind": "task", "task": self.task},
            {
                "kind": "stub",
                "message": (
                    "Agent.run() stub: provider/HITL/driver loop not wired. "
                    "Host Windows required for real observe/act."
                ),
            },
        ]
        recording = await session.end()
        return AgentResult(
            ok=False,
            steps=steps,
            recording_path=recording,
            message="stub — not executed on desktop",
        )
