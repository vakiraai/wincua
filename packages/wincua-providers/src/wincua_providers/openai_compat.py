"""OpenAI-compatible ``/chat/completions`` providers.

* :class:`OpenAICompatProvider` — any OpenAI-compatible server (vLLM, LM Studio, …).
* :class:`LlamaCppProvider` — ``llama-server`` (llama.cpp); appends ``/v1``.
* :class:`OpenAIProvider` — api.openai.com with ``OPENAI_API_KEY``.
"""

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


class OpenAICompatProvider(HTTPChatProvider):
    name = "openai_compat"
    default_base_url = "http://127.0.0.1:8080/v1"
    default_model = "local-vlm"
    max_tokens_field = "max_tokens"

    def _headers(self) -> dict[str, str]:
        key = self._require_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

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
                        "content": turn.text or None,
                        "tool_calls": [
                            {
                                "id": c.call_id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": json.dumps(clean_args(c.args)),
                                },
                            }
                            for c in turn.calls
                        ],
                    }
                )
                for c in turn.calls:
                    messages.append(
                        {"role": "tool", "tool_call_id": c.call_id, "content": c.result}
                    )
            else:
                for c in turn.calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": json.dumps({"tool": c.name, "args": clean_args(c.args)}),
                        }
                    )
                    messages.append({"role": "user", "content": f"Result of {c.name}: {c.result}"})
        text = user_turn_text(task, observation, first=not turns, notes=orphan_notes(turns))
        img = screenshot_b64(observation) if self.send_screenshot else None
        if img:
            content: Any = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
            ]
        else:
            content = text
        messages.append({"role": "user", "content": content})
        return messages

    def build_payload(
        self, task: str, observation: Any, history: Sequence[Any] | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.build_messages(task, observation, history),
        }
        if self.use_native_tools:
            payload["tools"] = openai_tools(self.tools)
            payload["tool_choice"] = "auto"
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens:
            payload[self.max_tokens_field] = self.max_tokens
        return payload

    async def _chat(
        self, *, task: str, observation: Any | None, history: Sequence[Any] | None
    ) -> ProviderResult:
        headers = self._headers()
        url = f"{self.base_url}/chat/completions"
        for _attempt in range(3):
            resp = await self._request(
                "POST", url, self.build_payload(task, observation, history), headers
            )
            if not self._downgrade(resp):
                break
        raise_for_status(resp, label=self.name)
        data = resp.json() or {}
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else None) or {}
        text = message.get("content") or ""
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        steps = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            if fn.get("name"):
                steps.extend(
                    to_plan_steps(fn["name"], parse_arguments(fn.get("arguments")), call.get("id"))
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
            resp = await self._request(
                "GET", f"{self.base_url}/models", headers=self._headers(), timeout_s=5.0
            )
            raise_for_status(resp, label=self.name)
        except ProviderUnavailable as exc:
            return False, str(exc)
        return True, f"{self.name} ok at {self.base_url}"


class LlamaCppProvider(OpenAICompatProvider):
    """llama.cpp ``llama-server``.

    Start it with ``--jinja`` for native tool calls and ``--mmproj`` for images;
    without them the provider falls back to JSON replies / text-only observations.
    """

    name = "llamacpp"
    default_base_url = "http://127.0.0.1:8080"
    default_model = "local"

    def __init__(self, model: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(model, base_url, **kwargs)
        if not self.base_url.endswith("/v1"):
            self.base_url = f"{self.base_url}/v1"

    async def probe(self) -> tuple[bool, str]:
        root = self.base_url[: -len("/v1")]
        try:
            resp = await self._request("GET", f"{root}/health", timeout_s=5.0)
            raise_for_status(resp, label=self.name)
        except ProviderUnavailable as exc:
            return False, str(exc)
        return True, f"llama-server ok at {root}"


class OpenAIProvider(OpenAICompatProvider):
    name = "openai"
    default_base_url = "https://api.openai.com/v1"
    default_model = "gpt-4.1"
    default_api_key_env = "OPENAI_API_KEY"
    requires_api_key = True
    max_tokens_field = "max_completion_tokens"

    def __init__(self, model: str | None = None, base_url: str | None = None, **kwargs: Any):
        kwargs.setdefault("tool_mode", "native")
        super().__init__(model, base_url, **kwargs)
