"""NullProvider — always unavailable; agent routes to ask_human / HITL."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_providers.base import PlanStep, ProviderResult


class NullProvider:
    """No model. Agent must not rewrite the task — use HITL instead.

    Every turn asks the human for the next step (``mode="next_step"``): the
    human picks an action, types a command, takes over, or ends the task.
    """

    name = "null"

    async def plan(
        self,
        *,
        task: str,
        observation: Any | None = None,
        history: Sequence[Any] | None = None,
    ) -> ProviderResult:
        return ProviderResult(
            available=False,
            error="NullProvider: no model configured",
            steps=[
                PlanStep(
                    kind="ask_human",
                    name="ask_human",
                    args={
                        "question": f"No model available. Choose the next step for: {task}",
                        "risk": "info",
                        "mode": "next_step",
                    },
                )
            ],
        )
