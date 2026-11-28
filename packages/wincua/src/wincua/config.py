"""wincua config file (``wincua.toml``): lookup, schema and provider construction.

Lookup order (first hit wins):

1. explicit path (``load_config(path)`` / ``--config``)
2. ``WINCUA_CONFIG`` env var
3. ``./wincua.toml``
4. ``%APPDATA%\\wincua\\config.toml``

No file → built-in defaults (provider ``null`` = ask the human).
API keys are never read from the file: a provider names the env var
(``api_key_env``) that holds its key.

This module owns ``[provider]``, ``[providers.*]`` and ``[driver]``. Other
packages read their own sections (``[hitl]``, ``[mcp]``) from
:attr:`WincuaConfig.raw`.

Run ``python -m wincua.config [--config PATH] [--probe]`` to see which file is
used, the provider chain and whether each provider is reachable.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

ENV_VAR = "WINCUA_CONFIG"
LOCAL_FILENAME = "wincua.toml"

BUILTIN_PROVIDERS = ("ollama", "llamacpp", "anthropic", "openai", "openai_compat", "null", "scripted")

# Built-in defaults per provider (mirrors wincua.example.toml).
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "ollama": {"base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b"},
    "llamacpp": {"base_url": "http://127.0.0.1:8080", "model": "local"},
    "anthropic": {"model": "claude-sonnet-4-5", "api_key_env": "ANTHROPIC_API_KEY"},
    "openai": {"model": "gpt-4.1", "api_key_env": "OPENAI_API_KEY"},
    "openai_compat": {},
    "null": {},
    "scripted": {},
}

_FORBIDDEN_SECRET_KEYS = ("api_key", "key", "token", "secret", "password")


class ConfigError(ValueError):
    """Invalid or missing config file."""


@dataclass
class ProviderConfig:
    """One ``[providers.<name>]`` table merged over the built-in defaults."""

    name: str
    type: str
    base_url: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) or None if self.api_key_env else None

    def settings(self) -> dict[str, Any]:
        """Keyword arguments for ``wincua_providers.build_provider``."""
        out = dict(self.options)
        for key in ("base_url", "model", "api_key_env"):
            val = getattr(self, key)
            if val:
                out[key] = val
        return out


@dataclass
class ProviderSelection:
    """``[provider]`` table."""

    default: str = "null"
    fallback: list[str] = field(default_factory=list)


@dataclass
class DriverConfig:
    """``[driver]`` table. ``backend=None`` = auto (windows on Win32, else current)."""

    backend: str | None = None
    allow: list[str] = field(default_factory=list)
    launch_timeout_s: float | None = None


@dataclass
class WincuaConfig:
    provider: ProviderSelection = field(default_factory=ProviderSelection)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    driver: DriverConfig = field(default_factory=DriverConfig)
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def provider_config(self, name: str) -> ProviderConfig:
        if name in self.providers:
            return self.providers[name]
        if name in BUILTIN_PROVIDERS:
            return _provider_config(name, {})
        raise ConfigError(f"unknown provider {name!r}; known: {sorted(self.known_providers())}")

    def known_providers(self) -> set[str]:
        return set(BUILTIN_PROVIDERS) | set(self.providers)

    def chain(self, provider: str | None = None) -> list[str]:
        """Provider names to try, in order: default (or override), fallback, then ``null``."""
        names = [provider or self.provider.default, *self.provider.fallback]
        out: list[str] = []
        for name in names:
            if name not in out:
                out.append(name)
            if name == "null":
                break
        if "null" not in out:
            out.append("null")
        return out

    def build_provider(self, provider: str | None = None, *, transport: Any = None) -> Any:
        """Build the provider chain as a ``FallbackProvider`` (or a lone provider).

        ``provider`` overrides ``[provider].default`` (e.g. from ``--provider``).
        """
        from wincua_providers import FallbackProvider, build_provider

        names = self.chain(provider)
        built = []
        for name in names:
            pc = self.provider_config(name)
            extra = {"transport": transport} if transport is not None and pc.type not in {"null", "scripted"} else {}
            try:
                built.append(build_provider(pc.type, pc.settings(), **extra))
            except TypeError as exc:
                where = f" in {self.path}" if self.path else ""
                raise ConfigError(f"[providers.{name}]{where}: {exc}") from exc
        if len(built) == 1:
            return built[0]
        return FallbackProvider(built)


def find_config_path(path: str | os.PathLike[str] | None = None) -> Path | None:
    """Resolve the config file using the documented lookup order."""
    if path is not None:
        p = Path(path).expanduser()
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        return p
    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            raise ConfigError(f"{ENV_VAR} points to a missing file: {p}")
        return p
    local = Path.cwd() / LOCAL_FILENAME
    if local.is_file():
        return local
    appdata = os.environ.get("APPDATA")
    if appdata:
        p = Path(appdata) / "wincua" / "config.toml"
        if p.is_file():
            return p
    return None


def load_config(path: str | os.PathLike[str] | None = None) -> WincuaConfig:
    """Load ``wincua.toml`` (see module docstring for the lookup order)."""
    found = find_config_path(path)
    if found is None:
        return WincuaConfig()
    try:
        with found.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{found}: invalid TOML: {exc}") from exc
    try:
        cfg = parse_config(raw)
    except ConfigError as exc:
        raise ConfigError(f"{found}: {exc}") from exc
    cfg.path = found
    return cfg


def parse_config(raw: Mapping[str, Any]) -> WincuaConfig:
    """Validate a parsed TOML dict into :class:`WincuaConfig`."""
    raw = dict(raw)
    providers_raw = raw.get("providers", {})
    if not isinstance(providers_raw, dict):
        raise ConfigError("[providers] must be a table")
    providers: dict[str, ProviderConfig] = {}
    for name, table in providers_raw.items():
        if not isinstance(table, dict):
            raise ConfigError(f"[providers.{name}] must be a table")
        providers[name] = _provider_config(name, table)

    sel_raw = raw.get("provider", {})
    if not isinstance(sel_raw, dict):
        raise ConfigError("[provider] must be a table")
    default = sel_raw.get("default", "null")
    fallback = sel_raw.get("fallback", [])
    if isinstance(fallback, str):
        fallback = [fallback]
    if not isinstance(default, str) or not isinstance(fallback, list):
        raise ConfigError("[provider] default must be a string and fallback a list of strings")
    known = set(BUILTIN_PROVIDERS) | set(providers)
    for name in [default, *fallback]:
        if name not in known:
            raise ConfigError(f"[provider] references unknown provider {name!r}; known: {sorted(known)}")
    selection = ProviderSelection(default=default, fallback=[str(n) for n in fallback])

    drv = raw.get("driver", {})
    if not isinstance(drv, dict):
        raise ConfigError("[driver] must be a table")
    backend = drv.get("backend")
    if backend is not None and backend not in {"windows", "mock", "null"}:
        raise ConfigError(f"[driver] backend must be windows | mock | null, got {backend!r}")
    allow = drv.get("allow", [])
    if not isinstance(allow, list):
        raise ConfigError("[driver] allow must be a list of process names")
    timeout = drv.get("launch_timeout_s")
    driver = DriverConfig(
        backend=backend,
        allow=[str(a) for a in allow],
        launch_timeout_s=float(timeout) if timeout is not None else None,
    )
    return WincuaConfig(provider=selection, providers=providers, driver=driver, raw=raw)


def _provider_config(name: str, table: Mapping[str, Any]) -> ProviderConfig:
    for key in table:
        if key.lower() in _FORBIDDEN_SECRET_KEYS:
            raise ConfigError(
                f"[providers.{name}] must not contain {key!r}: API keys are never stored in "
                "the config file; set api_key_env to the name of an environment variable"
            )
    ptype = str(table.get("type", name))
    if ptype not in BUILTIN_PROVIDERS:
        raise ConfigError(
            f"[providers.{name}] type {ptype!r} is not a provider type; expected one of {list(BUILTIN_PROVIDERS)}"
        )
    merged = {**PROVIDER_DEFAULTS.get(ptype, {}), **PROVIDER_DEFAULTS.get(name, {}), **dict(table)}
    merged.pop("type", None)
    base_url = merged.pop("base_url", None) or None
    model = merged.pop("model", None) or None
    api_key_env = merged.pop("api_key_env", None) or None
    return ProviderConfig(
        name=name,
        type=ptype,
        base_url=base_url,
        model=model,
        api_key_env=api_key_env,
        options=merged,
    )


def describe(cfg: WincuaConfig, provider: str | None = None) -> str:
    lines = [f"config file: {cfg.path or '(none — built-in defaults)'}"]
    lines.append(f"provider chain: {' -> '.join(cfg.chain(provider))}")
    for name in cfg.chain(provider):
        pc = cfg.provider_config(name)
        if pc.type in {"null", "scripted"}:
            lines.append(f"  {name}: {pc.type}")
            continue
        key = ""
        if pc.api_key_env:
            key = f", key env {pc.api_key_env} ({'set' if pc.api_key else 'NOT set'})"
        lines.append(f"  {name}: type={pc.type} model={pc.model!r} base_url={pc.base_url or '(default)'}{key}")
    return "\n".join(lines)


async def probe_chain(cfg: WincuaConfig, provider: str | None = None) -> list[tuple[str, bool, str]]:
    """Reachability check for each provider in the chain (no tokens spent)."""
    from wincua_providers import build_provider

    out: list[tuple[str, bool, str]] = []
    for name in cfg.chain(provider):
        pc = cfg.provider_config(name)
        if pc.type == "null":
            out.append((name, True, "always available: asks the human (HITL)"))
            continue
        if pc.type == "scripted":
            out.append((name, True, "scripted plan"))
            continue
        prov = build_provider(pc.type, pc.settings(), retries=0)
        ok, detail = await prov.probe()
        out.append((name, ok, detail))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(prog="python -m wincua.config", description="Show the resolved wincua config")
    parser.add_argument("--config", help="Path to wincua.toml")
    parser.add_argument("--provider", help="Override [provider].default")
    parser.add_argument("--probe", action="store_true", help="Check each provider in the chain is reachable")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        print(describe(cfg, args.provider))
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 1
    if args.probe:
        first_ok = None
        for name, ok, detail in asyncio.run(probe_chain(cfg, args.provider)):
            print(f"  [{'ok' if ok else 'unavailable'}] {name}: {detail}")
            if ok and first_ok is None:
                first_ok = name
        print(f"would plan with: {first_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
