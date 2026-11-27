"""Build providers by type name from plain settings dicts (e.g. a config table)."""

from __future__ import annotations

from typing import Any, Mapping

from wincua_providers.anthropic import AnthropicProvider
from wincua_providers.null import NullProvider
from wincua_providers.ollama import OllamaProvider
from wincua_providers.openai_compat import LlamaCppProvider, OpenAICompatProvider, OpenAIProvider
from wincua_providers.scripted import ScriptedProvider

PROVIDER_TYPES: dict[str, type] = {
    "ollama": OllamaProvider,
    "llamacpp": LlamaCppProvider,
    "openai_compat": OpenAICompatProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "null": NullProvider,
    "scripted": ScriptedProvider,
}


def build_provider(kind: str, settings: Mapping[str, Any] | None = None, **overrides: Any) -> Any:
    """Instantiate a provider of ``kind`` with ``settings`` (empty values ignored)."""
    if kind not in PROVIDER_TYPES:
        raise ValueError(f"unknown provider type {kind!r}; expected one of {sorted(PROVIDER_TYPES)}")
    opts = {k: v for k, v in dict(settings or {}).items() if v not in (None, "")}
    opts.update(overrides)
    opts.pop("type", None)
    if kind == "null":
        return NullProvider()
    if kind == "scripted":
        return ScriptedProvider(opts.get("steps") or [], steps_per_call=int(opts.get("steps_per_call", 1)))
    return PROVIDER_TYPES[kind](**opts)
