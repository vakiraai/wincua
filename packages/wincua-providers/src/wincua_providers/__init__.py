"""wincua-providers — planner provider bus."""

from wincua_providers._http import HTTPResponse, ProviderUnavailable
from wincua_providers.anthropic import AnthropicProvider
from wincua_providers.base import PlanStep, Provider, ProviderResult
from wincua_providers.fallback import FallbackProvider
from wincua_providers.null import NullProvider
from wincua_providers.ollama import OllamaProvider
from wincua_providers.openai_compat import LlamaCppProvider, OpenAICompatProvider, OpenAIProvider
from wincua_providers.registry import PROVIDER_TYPES, build_provider
from wincua_providers.scripted import ScriptedProvider

# Name used in the architecture plan for the computer-use capable adapter.
AnthropicCUProvider = AnthropicProvider

__all__ = [
    "PROVIDER_TYPES",
    "AnthropicCUProvider",
    "AnthropicProvider",
    "FallbackProvider",
    "HTTPResponse",
    "LlamaCppProvider",
    "NullProvider",
    "OllamaProvider",
    "OpenAICompatProvider",
    "OpenAIProvider",
    "PlanStep",
    "Provider",
    "ProviderResult",
    "ProviderUnavailable",
    "ScriptedProvider",
    "build_provider",
]

__version__ = "0.1.0"
