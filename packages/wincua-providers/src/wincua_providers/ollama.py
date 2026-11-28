"""OllamaProvider — local VLM via Ollama's native ``/api/chat`` (images + tool calls)."""

from __future__ import annotations

import json
from typing import Any, Sequence

from wincua_providers._http import ProviderUnavailable, raise_for_status
from wincua_providers.base import ProviderResult
from wincua_providers.chat import HTTPChatProvider
from wincua_providers.prompting import (
    NUDGE,
    clean_args,
    group_history,
    orphan_notes,
    screenshot_b64,
    user_turn_text,
)
from wincua_providers.tools import (
    json_mode_instructions,
    openai_tools,
    parse_arguments,
    to_plan_steps,
)


class OllamaProvider(HTTPChatProvider):
    """Ollama native chat API.

    ``tool_mode="auto"`` sends native ``tools`` and falls back to JSON replies
    (``format: "json"``) when the model reports it does not support tools —
    common for vision models such as ``qwen2.5vl``.
    """

    name = "ollama"
    default_base_url = "http://127.0.0.1:11434"
    default_model = "qwen2.5:7b"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        *,
        num_ctx: int | None = None,
        keep_alive: str | None = None,
        think: bool | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model, base_url, **kwargs)
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.think = think

    def build_messages(
        self, task: str, observation: Any, history: Sequence[Any] | None
    ) -> list[dict[str, Any]]:
        native = self.use_native_tools
        system = self.system_prompt
        if not native:
            system = f"{system}\n\n{json_mode_instructions(self.tools)}"
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        turns = group_history(history)
        if turns:
            messages.append({"role": "user", "content": f"Task: {task}"})
        for turn in turns:
            if turn.text is None and not turn.calls:
                continue
            if turn.text is not None and not turn.calls:
                messages.append({"role": "assistant", "content": turn.text})
                messages.append({"role": "user", "content": NUDGE})
                continue
            if native:
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn.text or "",
                        "tool_calls": [
                            {"function": {"name": c.name, "arguments": clean_args(c.args)}}
                            for c in turn.calls
                        ],
                    }
                )
                for c in turn.calls:
                    messages.append({"role": "tool", "tool_name": c.name, "content": c.result})
            else:
                for c in turn.calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": json.dumps({"tool": c.name, "args": clean_args(c.args)}),
                        }
                    )
                    messages.append({"role": "user", "content": f"Result of {c.name}: {c.result}"})
        final: dict[str, Any] = {
            "role": "user",
            "content": user_turn_text(task, observation, first=not turns, notes=orphan_notes(turns)),
        }
        img = screenshot_b64(observation) if self.send_screenshot else None
        if img:
            final["images"] = [img]
        messages.append(final)
        return messages

    def build_payload(
        self, task: str, observation: Any, history: Sequence[Any] | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.build_messages(task, observation, history),
            "stream": False,
        }
        if self.use_native_tools:
            payload["tools"] = openai_tools(self.tools)
        else:
            payload["format"] = "json"
        opts: dict[str, Any] = {}
        if self.temperature is not None:
            opts["temperature"] = self.temperature
        if self.num_ctx:
            opts["num_ctx"] = self.num_ctx
        if self.max_tokens:
            opts["num_predict"] = self.max_tokens
        if opts:
            payload["options"] = opts
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        if self.think is not None:
            payload["think"] = self.think
        return payload

    async def _chat(
        self, *, task: str, observation: Any | None, history: Sequence[Any] | None
    ) -> ProviderResult:
        url = f"{self.base_url}/api/chat"
        for _attempt in range(3):
            resp = await self._request(
                "POST", url, self.build_payload(task, observation, history)
            )
            if not self._downgrade(resp):
                break
        if resp.status == 404:
            raise_for_status(resp, label=self.name, hint=f"run: ollama pull {self.model}")
        raise_for_status(resp, label=self.name)
        data = resp.json() or {}
        message = data.get("message") or {}
        text = message.get("content") or ""
        steps = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            name = fn.get("name")
            if name:
                steps.extend(
                    to_plan_steps(name, parse_arguments(fn.get("arguments")), call.get("id") or None)
                )
        if not steps:
            steps = self._steps_from_text(text)
        return ProviderResult(
            steps=steps,
            raw=data,
            text=text or None,
            error=None if steps else "model returned no tool call",
        )

    async def probe(self) -> tuple[bool, str]:
        try:
            resp = await self._request("GET", f"{self.base_url}/api/tags", timeout_s=5.0)
            raise_for_status(resp, label=self.name)
        except ProviderUnavailable as exc:
            return False, str(exc)
        names = {m.get("name") or m.get("model") for m in (resp.json() or {}).get("models", [])}
        wanted = {self.model, f"{self.model}:latest"}
        if not names & wanted:
            return False, (
                f"ollama reachable at {self.base_url} but model {self.model!r} is not pulled "
                f"(run: ollama pull {self.model})"
            )
        return True, f"ollama ok at {self.base_url}, model {self.model!r} present"

