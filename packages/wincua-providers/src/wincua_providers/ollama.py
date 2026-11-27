"""OllamaProvider stub — local VLM via Ollama HTTP API (wired in Phase 0/1)."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_providers.base import PlanStep, ProviderResult


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        model: str = "qwen2.5-vl:7b",
        base_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def plan(
        self,
        *,
        task: str,
        observation: Any | None = None,
        history: Sequence[Any] | None = None,
    ) -> ProviderResult:
        # Stub: real HTTP tool-calling lands in Phase 0 spike.
        return ProviderResult(
            available=False,
            error=(
                f"OllamaProvider stub: not wired yet "
                f"(model={self.model!r}, base_url={self.base_url!r})"
            ),
            steps=[
                PlanStep(
                    kind="ask_human",
                    name="ask_human",
                    args={
                        "question": (
                            "Ollama provider is a stub. Continue via HITL for: "
                            f"{task}"
                        ),
                        "risk": "info",
                    },
                )
            ],
        )
