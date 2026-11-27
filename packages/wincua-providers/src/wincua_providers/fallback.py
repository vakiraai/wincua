"""FallbackProvider — try providers in order; move on when one is unavailable."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_providers.base import ProviderResult


class FallbackProvider:
    """Wrap an ordered provider chain (e.g. ollama → llamacpp → null).

    The active provider is used until it reports ``available=False``
    (unreachable, missing key, auth/model error); then the next one takes over
    for the rest of the run. ``null`` ends the chain: it routes planning to
    the human through HITL.
    """

    def __init__(self, providers: Sequence[Any]) -> None:
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self.providers = list(providers)
        self._index = 0
        self.events: list[str] = []

    @property
    def active(self) -> Any:
        return self.providers[self._index]

    @property
    def name(self) -> str:
        return getattr(self.active, "name", type(self.active).__name__)

    @property
    def chain(self) -> list[str]:
        return [getattr(p, "name", type(p).__name__) for p in self.providers]

    @property
    def recovers_from_errors(self) -> bool:
        return bool(getattr(self.active, "recovers_from_errors", False))

    def describe(self) -> str:
        return " -> ".join(self.chain)

    def escalate(self, reason: str) -> bool:
        """Hand over to the next provider (e.g. after a loop). False if none is left."""
        if self._index >= len(self.providers) - 1:
            return False
        nxt = getattr(self.providers[self._index + 1], "name", "?")
        self.events.append(f"{self.name} escalated ({reason}); handing over to {nxt}")
        self._index += 1
        return True

    async def plan(
        self,
        *,
        task: str,
        observation: Any | None = None,
        history: Sequence[Any] | None = None,
    ) -> ProviderResult:
        notes: list[str] = []
        while True:
            provider = self.active
            pname = getattr(provider, "name", type(provider).__name__)
            result = await provider.plan(task=task, observation=observation, history=history)
            result.provider = result.provider or pname
            last = self._index >= len(self.providers) - 1
            if result.available or last or pname == "null":
                if notes:
                    prefix = "; ".join(notes)
                    result.error = f"{prefix}; {result.error}" if result.error else prefix
                return result
            nxt = getattr(self.providers[self._index + 1], "name", "?")
            note = f"{pname} unavailable ({result.error}); falling back to {nxt}"
            notes.append(note)
            self.events.append(note)
            self._index += 1
