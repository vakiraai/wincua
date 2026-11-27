"""Provider protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass
class PlanStep:
    """One planner decision — tool call or done/abort."""

    kind: str  # tool | done | abort | ask_human
    name: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    message: str | None = None
    call_id: str | None = None  # model tool-call id, echoed back with the result


@dataclass
class ProviderResult:
    steps: list[PlanStep] = field(default_factory=list)
    raw: Any = None
    available: bool = True
    error: str | None = None
    provider: str | None = None  # name of the provider that produced this result
    text: str | None = None  # free text the model returned alongside / instead of tool calls


class Provider(Protocol):
    name: str

    async def plan(
        self,
        *,
        task: str,
        observation: Any | None = None,
        history: Sequence[Any] | None = None,
    ) -> ProviderResult: ...
