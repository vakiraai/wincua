"""OpenAICompatProvider stub — any OpenAI-compatible local/cloud endpoint."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_providers.base import PlanStep, ProviderResult


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(
        self,
        model: str = "local-vlm",
        base_url: str = "http://127.0.0.1:8080/v1",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def plan(
        self,
        *,
        task: str,
        observation: Any | None = None,
        history: Sequence[Any] | None = None,
    ) -> ProviderResult:
        return ProviderResult(
            available=False,
            error=(
                f"OpenAICompatProvider stub: not wired yet "
                f"(model={self.model!r}, base_url={self.base_url!r})"
            ),
            steps=[
                PlanStep(
                    kind="ask_human",
                    name="ask_human",
                    args={
                        "question": (
                            "OpenAI-compat provider is a stub. Continue via HITL for: "
                            f"{task}"
                        ),
                        "risk": "info",
                    },
                )
            ],
        )
