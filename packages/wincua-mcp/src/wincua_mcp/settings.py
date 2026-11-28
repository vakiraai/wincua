"""``[mcp]`` settings for wincua-mcp, parsed from ``wincua.toml``.

The ``[driver]`` section is owned by ``wincua.config``; it is only read here
(backend name, process allow-list, launch timeout) so the MCP server can start
the driver without depending on the ``wincua`` package.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

Transport = Literal["stdio", "http"]

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8766
DEFAULT_BACKEND = "windows"
_TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "http": "http",
    "streamable-http": "http",
    "streamable_http": "http",
}
_BACKENDS = ("windows", "mock", "null")


class SettingsError(ValueError):
    """Invalid or unreadable wincua config."""


@dataclass
class McpSettings:
    transport: Transport = "stdio"
    http_host: str = DEFAULT_HTTP_HOST
    http_port: int = DEFAULT_HTTP_PORT
    allow: list[str] = field(default_factory=list)
    require_confirmation: bool = True
    confirm_timeout_s: float = 120.0
    max_elements: int = 150

    # Read-only view of [driver]
    backend: str = DEFAULT_BACKEND
    driver_allow: list[str] = field(default_factory=list)
    launch_timeout_s: float | None = None

    config_path: Path | None = None

    @property
    def effective_allow(self) -> list[str]:
        """``[driver].allow`` plus ``[mcp].allow``, de-duplicated, order kept."""
        seen: set[str] = set()
        out: list[str] = []
        for entry in [*self.driver_allow, *self.allow]:
            key = entry.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(entry.strip())
        return out


def find_config_path(path: str | os.PathLike[str] | None = None) -> Path | None:
    """Config lookup: explicit path → $WINCUA_CONFIG → ./wincua.toml → %APPDATA%\\wincua\\config.toml."""
    if path is not None:
        p = Path(path).expanduser()
        if not p.is_file():
            raise SettingsError(f"Config file not found: {p}")
        return p
    env = os.environ.get("WINCUA_CONFIG")
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            raise SettingsError(f"WINCUA_CONFIG points to a missing file: {p}")
        return p
    local = Path.cwd() / "wincua.toml"
    if local.is_file():
        return local
    appdata = os.environ.get("APPDATA")
    if appdata:
        p = Path(appdata) / "wincua" / "config.toml"
        if p.is_file():
            return p
    return None


def read_raw_config(path: str | os.PathLike[str] | None = None) -> tuple[dict[str, Any], Path | None]:
    found = find_config_path(path)
    if found is None:
        return {}, None
    try:
        with found.open("rb") as fh:
            return tomllib.load(fh), found
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"Invalid TOML in {found}: {exc}") from exc


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, Mapping):
        raise SettingsError(f"[{name}] must be a table")
    return value


def _str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SettingsError(f"{where} must be a list of strings")
    return [v for v in value if v.strip()]


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsError(f"{where} must be true or false")
    return value


def _number(value: Any, where: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise SettingsError(f"{where} must be a number >= {minimum}")
    return float(value)


def parse_settings(raw: Mapping[str, Any], *, config_path: Path | None = None) -> McpSettings:
    """Build settings from the raw TOML dict (as returned by ``tomllib`` or ``WincuaConfig.raw``)."""
    mcp = _section(raw, "mcp")
    driver = _section(raw, "driver")
    s = McpSettings(config_path=config_path)

    if "transport" in mcp:
        t = str(mcp["transport"]).strip().lower()
        if t not in _TRANSPORT_ALIASES:
            raise SettingsError(f"[mcp].transport must be 'stdio' or 'http', got {mcp['transport']!r}")
        s.transport = _TRANSPORT_ALIASES[t]  # type: ignore[assignment]
    if "http_host" in mcp:
        if not isinstance(mcp["http_host"], str) or not mcp["http_host"].strip():
            raise SettingsError("[mcp].http_host must be a non-empty string")
        s.http_host = mcp["http_host"].strip()
    if "http_port" in mcp:
        port = mcp["http_port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise SettingsError(f"[mcp].http_port must be an integer 1-65535, got {port!r}")
        s.http_port = port
    s.allow = _str_list(mcp.get("allow"), "[mcp].allow")
    if "require_confirmation" in mcp:
        s.require_confirmation = _bool(mcp["require_confirmation"], "[mcp].require_confirmation")
    if "confirm_timeout_s" in mcp:
        s.confirm_timeout_s = _number(mcp["confirm_timeout_s"], "[mcp].confirm_timeout_s", minimum=1)
    if "max_elements" in mcp:
        s.max_elements = int(_number(mcp["max_elements"], "[mcp].max_elements", minimum=1))

    if "backend" in driver:
        backend = str(driver["backend"]).strip().lower()
        if backend not in _BACKENDS:
            raise SettingsError(f"[driver].backend must be one of {_BACKENDS}, got {driver['backend']!r}")
        s.backend = backend
    s.driver_allow = _str_list(driver.get("allow"), "[driver].allow")
    if "launch_timeout_s" in driver:
        s.launch_timeout_s = _number(driver["launch_timeout_s"], "[driver].launch_timeout_s", minimum=1)
    return s


def load_settings(path: str | os.PathLike[str] | None = None) -> McpSettings:
    raw, found = read_raw_config(path)
    return parse_settings(raw, config_path=found)
