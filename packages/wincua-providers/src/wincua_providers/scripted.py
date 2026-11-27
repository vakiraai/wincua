"""ScriptedProvider — deterministic plan steps for demos without an LLM."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_providers.base import PlanStep, ProviderResult


class ScriptedProvider:
    """Replay a fixed list of PlanSteps, one (or N) per plan() call.

    When the script is exhausted, returns a single ``done`` step.
    """

    name = "scripted"

    def __init__(
        self,
        steps: Sequence[PlanStep | dict[str, Any]],
        *,
        steps_per_call: int = 1,
    ) -> None:
        self._steps: list[PlanStep] = [
            s if isinstance(s, PlanStep) else PlanStep(**s) for s in steps
        ]
        self._i = 0
        self._steps_per_call = max(1, steps_per_call)

    @property
    def remaining(self) -> int:
        return max(0, len(self._steps) - self._i)

    async def plan(
        self,
        *,
        task: str,
        observation: Any | None = None,
        history: Sequence[Any] | None = None,
    ) -> ProviderResult:
        _ = (task, observation, history)
        if self._i >= len(self._steps):
            return ProviderResult(
                available=True,
                steps=[PlanStep(kind="done", message="script complete")],
            )
        end = min(self._i + self._steps_per_call, len(self._steps))
        batch = self._steps[self._i : end]
        self._i = end
        return ProviderResult(available=True, steps=list(batch), raw={"index": self._i})
