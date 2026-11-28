"""OllamaProvider — local VLM via Ollama's native ``/api/chat`` (images + tool calls)."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_providers._http import ProviderUnavailable, raise_for_status
from wincua_providers.base import ProviderResult
from wincua_providers.chat import HTTPChatProvider
from wincua_providers.prompting import (
    NUDGE,
    clean_args,
    group_history,
    json_turn_messages,
    orphan_notes,
    screenshot_b64,
    user_turn_text,
)
from wincua_providers.tools import (
    JSON_REMINDER,
    actions_schema,
    json_mode_instructions,
    openai_tools,
    parse_arguments,
    to_plan_steps,
)


JSON_MODE_MAX_TOKENS = 512
# Ollama library models without the "tools" capability: start in JSON mode
# instead of spending the first request on a "does not support tools" error.
NO_TOOL_MODELS = ("qwen2.5vl", "llava", "llama3.2-vision", "minicpm-v", "moondream", "gemma3")


def lacks_native_tools(model: str) -> bool:
    base = model.rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return any(base == m or base.startswith(f"{m}-") for m in NO_TOOL_MODELS)


class OllamaProvider(HTTPChatProvider):
    """Ollama native chat API.

    ``tool_mode="auto"`` sends native ``tools`` and falls back to JSON replies
    when the model reports it does not support tools (or is a known vision-only
    model such as ``qwen2.5vl``). JSON replies are constrained to
    ``{"actions": [...]}`` with a JSON schema.
    """

    name = "ollama"
    default_base_url = "http://127.0.0.1:11434"
    default_model = "qwen2.5vl:7b"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        *,
        num_ctx: int | None = None,
        keep_alive: str | None = None,
        think: bool | None = None,
        json_schema: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(model, base_url, **kwargs)
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.think = think
        # JSON-reply mode: constrain replies to {"actions": [...]} with a schema
        # (Ollama structured outputs); plain "json" if the server rejects it.
        self.json_schema = bool(json_schema)
        if self.tool_mode == "auto" and lacks_native_tools(self.model):
            self.tool_mode = "json"

    def _downgrade(self, resp: Any) -> bool:
        if super()._downgrade(resp):
            return True
        if resp.status < 400 or self.use_native_tools or not self.json_schema:
            return False
        body = resp.text(2000).lower()
        if "format" in body or "schema" in body:
            self.json_schema = False
            return True
        return False

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
                messages.append({"role": "user", "content": NUDGE if native else JSON_REMINDER})
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
                messages.extend(json_turn_messages(turn))
        content = user_turn_text(task, observation, first=not turns, notes=orphan_notes(turns))
        if not native:
            content = f"{content}\n\n{JSON_REMINDER}"
        final: dict[str, Any] = {"role": "user", "content": content}
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
            payload["format"] = actions_schema(self.tools) if self.json_schema else "json"
        opts: dict[str, Any] = {}
        if self.temperature is not None:
            opts["temperature"] = self.temperature
        if self.num_ctx:
            opts["num_ctx"] = self.num_ctx
        if self.max_tokens:
            opts["num_predict"] = self.max_tokens
        elif not self.use_native_tools:
            # JSON-constrained output can pad with whitespace until the limit.
            opts["num_predict"] = JSON_MODE_MAX_TOKENS
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

    async def warm_up(self) -> tuple[bool, str]:
        """Load the model into memory (empty prompt, no tokens) so turn 1 isn't a cold start."""
        payload: dict[str, Any] = {"model": self.model, "prompt": "", "stream": False}
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        try:
            resp = await self._request("POST", f"{self.base_url}/api/generate", payload)
            if resp.status == 404:
                raise_for_status(resp, label=self.name, hint=f"run: ollama pull {self.model}")
            raise_for_status(resp, label=self.name)
        except ProviderUnavailable as exc:
            return False, str(exc)
        return True, f"model {self.model!r} loaded"

