"""FallbackProvider — try providers in order; move on when one is unavailable."""

from __future__ import annotations

import sys
from typing import Any, Callable, Sequence

from wincua_providers.base import ProviderResult

REASON_LIMIT = 200


def short_reason(text: str | None, limit: int = REASON_LIMIT) -> str:
    one_line = " ".join(str(text or "no reason given").split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def print_event(line: str) -> None:
    print(f"[wincua] {line}", file=sys.stderr, flush=True)


class FallbackProvider:
    """Wrap an ordered provider chain (e.g. ollama → llamacpp → null).

    The active provider is used until it reports ``available=False``
    (unreachable, missing key, auth/model error); then the next one takes over
    for the rest of the run. ``null`` ends the chain: it routes planning to
    the human through HITL.
    """

    def __init__(self, providers: Sequence[Any], *, notify: Callable[[str], None] | None = print_event) -> None:
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self.providers = list(providers)
        self._index = 0
        self.events: list[str] = []
        # Called with each event as it happens; a run can block on a human
        # prompt right after a fallback, long before the events are printed.
        self.notify = notify

    def _event(self, line: str) -> None:
        self.events.append(line)
        if self.notify is not None:
            self.notify(line)

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
        self._event(f"{self.name} escalated ({short_reason(reason)}); handing over to {nxt}")
        self._index += 1
        return True

    def resume(self, reason: str) -> bool:
        """Go back to the first provider (e.g. after the human gave the model a hint)."""
        if self._index == 0:
            return False
        self._event(f"{self.name} handing back to {self.chain[0]} ({short_reason(reason)})")
        self._index = 0
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
                    _prefix_human_question(result, prefix)
                return result
            nxt = getattr(self.providers[self._index + 1], "name", "?")
            note = f"{pname} unavailable: {short_reason(result.error)}; falling back to {nxt}"
            notes.append(note)
            self._event(note)
            self._index += 1


def _prefix_human_question(result: ProviderResult, prefix: str) -> None:
    """Show why the model was skipped in the question the human is asked."""
    for step in result.steps:
        if step.kind == "ask_human" and isinstance(step.args, dict) and step.args.get("question"):
            step.args = {**step.args, "question": f"{prefix}\n{step.args['question']}"}
            return
