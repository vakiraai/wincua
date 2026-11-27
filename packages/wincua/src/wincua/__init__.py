"""wincua — Windows-host computer-use agent façade."""

from typing import Any

from wincua.agent import Agent, AgentResult
from wincua.session import Session

__all__ = ["Agent", "AgentResult", "ConfigError", "Session", "WincuaConfig", "load_config"]

__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    # Lazy so `python -m wincua.config` does not import the module twice.
    if name in {"ConfigError", "WincuaConfig", "load_config"}:
        from wincua import config

        return getattr(config, name)
    raise AttributeError(f"module 'wincua' has no attribute {name!r}")
