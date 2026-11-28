"""Shared base for HTTP chat-model providers (Ollama, OpenAI-compat, Anthropic)."""

from __future__ import annotations

import os
from typing import Any, Sequence

from wincua_providers._http import (
    HTTPResponse,
    ProviderUnavailable,
    Transport,
    request_json,
    urllib_transport,
)
from wincua_providers.base import PlanStep, ProviderResult
from wincua_providers.prompting import SYSTEM_PROMPT
from wincua_providers.tools import TOOL_SPECS, parse_json_tool_calls, to_plan_step

_TOOLS_UNSUPPORTED = (
    "does not support tools",
    "tools param requires",
    "--jinja",
    "tool_choice",
    "tools are not supported",
    "function calling",
)
_IMAGES_UNSUPPORTED = (
    "image input",
    "mmproj",
    "multimodal",
    "does not support images",
    "vision",
    "image_url",
)


class HTTPChatProvider:
    """Common plumbing: settings, API key lookup, retries, capability downgrade.

    Subclasses implement ``_chat(task, observation, history) -> ProviderResult``
    and may raise :class:`ProviderUnavailable`, which ``plan()`` converts into
    ``ProviderResult(available=False)`` so a fallback chain can move on.
    """

    name = "chat"
    default_base_url = ""
    default_model = ""
    default_api_key_env: str | None = None
    requires_api_key = False
    # LLM planners can read a failed tool result and try again.
    recovers_from_errors = True

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout_s: float = 120.0,
        temperature: float | None = 0.0,
        max_tokens: int | None = None,
        send_screenshot: bool = True,
        tool_mode: str = "auto",
        retries: int = 1,
        retry_backoff_s: float = 1.0,
        system_prompt: str | None = None,
        transport: Transport | None = None,
        **options: Any,
    ) -> None:
        if tool_mode not in {"auto", "native", "json"}:
            raise ValueError(f"tool_mode must be auto | native | json, got {tool_mode!r}")
        self.model = model or self.default_model
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._api_key = api_key
        self.api_key_env = api_key_env if api_key_env is not None else self.default_api_key_env
        self.timeout_s = float(timeout_s)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.send_screenshot = bool(send_screenshot)
        self.tool_mode = tool_mode
        self.retries = int(retries)
        self.retry_backoff_s = float(retry_backoff_s)
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.transport: Transport = transport or urllib_transport
        self.options = options
        self.tools = list(TOOL_SPECS)

    # -- settings -----------------------------------------------------------------

    @property
    def api_key(self) -> str | None:
        if self._api_key:
            return self._api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env) or None
        return None

    def _require_key(self) -> str | None:
        key = self.api_key
        if self.requires_api_key and not key:
            env = self.api_key_env or "<api_key_env>"
            raise ProviderUnavailable(f"{self.name}: API key env var {env} is not set")
        return key

    @property
    def use_native_tools(self) -> bool:
        return self.tool_mode != "json"

    def describe(self) -> str:
        return f"{self.name}(model={self.model!r}, base_url={self.base_url!r})"

    # -- HTTP ---------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> HTTPResponse:
        return await request_json(
            self.transport,
            method,
            url,
            payload=payload,
            headers=headers,
            timeout_s=timeout_s or self.timeout_s,
            retries=self.retries,
            retry_backoff_s=self.retry_backoff_s,
            label=self.name,
        )

    def _downgrade(self, resp: HTTPResponse) -> bool:
        """Adapt to servers without tool or image support. True if worth retrying."""
        if resp.status < 400:
            return False
        body = resp.text(2000).lower()
        if self.tool_mode == "auto" and any(s in body for s in _TOOLS_UNSUPPORTED):
            self.tool_mode = "json"
            return True
        if self.send_screenshot and any(s in body for s in _IMAGES_UNSUPPORTED):
            self.send_screenshot = False
            return True
        return False

    # -- planning -----------------------------------------------------------------

    async def plan(
        self,
        *,
        task: str,
        observation: Any | None = None,
        history: Sequence[Any] | None = None,
    ) -> ProviderResult:
        try:
            result = await self._chat(task=task, observation=observation, history=history)
        except ProviderUnavailable as exc:
            return ProviderResult(available=False, error=str(exc), provider=self.name)
        result.provider = self.name
        return result

    async def _chat(
        self, *, task: str, observation: Any | None, history: Sequence[Any] | None
    ) -> ProviderResult:
        raise NotImplementedError

    async def probe(self) -> tuple[bool, str]:
        """Cheap reachability check (no tokens spent). Returns (ok, detail)."""
        raise NotImplementedError

    def _steps_from_text(self, text: str | None) -> list[PlanStep]:
        return [to_plan_step(name, args) for name, args in parse_json_tool_calls(text or "")]
