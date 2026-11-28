"""``[hitl]`` section of ``wincua.toml``.

Parsed here from the raw config dict so this package does not depend on
``wincua.config``. Lookup order matches the shared config spec: explicit
path, ``WINCUA_CONFIG``, ``./wincua.toml``, ``%APPDATA%\\wincua\\config.toml``.
"""

from __future__ import annotations

import ipaddress
import os
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Mapping

HitlMode = Literal["ui", "cli", "off"]
MODES: tuple[str, ...] = ("ui", "cli", "off")


@dataclass(frozen=True)
class HitlSettings:
    mode: HitlMode = "ui"
    host: str = "127.0.0.1"
    port: int = 8765
    confirm_risky_actions: bool = True
    timeout_s: float = 300.0
    open_browser: bool = True
    source: str | None = None

    def with_overrides(self, **kwargs: Any) -> "HitlSettings":
        return _validate(replace(self, **{k: v for k, v in kwargs.items() if v is not None}))


def is_loopback_host(host: str) -> bool:
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _validate(s: HitlSettings) -> HitlSettings:
    if s.mode not in MODES:
        raise ValueError(f"[hitl] mode must be one of {MODES}, got {s.mode!r}")
    if not is_loopback_host(s.host):
        raise ValueError(
            f"[hitl] host must be a loopback address (127.0.0.1, ::1, localhost), got {s.host!r}"
        )
    if not isinstance(s.port, int) or isinstance(s.port, bool) or not 0 <= s.port <= 65535:
        raise ValueError(f"[hitl] port must be an integer 0-65535, got {s.port!r}")
    if not s.timeout_s or float(s.timeout_s) <= 0:
        raise ValueError(f"[hitl] timeout_s must be > 0, got {s.timeout_s!r}")
    return s


def find_config_path(path: str | os.PathLike[str] | None = None) -> Path | None:
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"wincua config not found: {p}")
        return p
    env = os.environ.get("WINCUA_CONFIG")
    if env:
        p = Path(env)
        if not p.is_file():
            raise FileNotFoundError(f"WINCUA_CONFIG points to a missing file: {p}")
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


def parse_hitl_settings(raw: Mapping[str, Any] | Any | None, *, source: str | None = None) -> HitlSettings:
    """Build settings from a full config dict (or a ``WincuaConfig`` with ``.raw``)."""
    if raw is not None and not isinstance(raw, Mapping):
        raw = getattr(raw, "raw", None)
    section = (raw or {}).get("hitl") or {}
    if not isinstance(section, Mapping):
        raise ValueError("[hitl] must be a table")
    known = {"mode", "host", "port", "confirm_risky_actions", "timeout_s", "open_browser"}
    unknown = set(section) - known
    if unknown:
        print(f"wincua-hitl: ignoring unknown [hitl] keys {sorted(unknown)}", file=sys.stderr)
    base = HitlSettings()
    s = HitlSettings(
        mode=str(section.get("mode", base.mode)).lower(),  # type: ignore[arg-type]
        host=str(section.get("host", base.host)),
        port=section.get("port", base.port),
        confirm_risky_actions=bool(section.get("confirm_risky_actions", base.confirm_risky_actions)),
        timeout_s=float(section.get("timeout_s", base.timeout_s)),
        open_browser=bool(section.get("open_browser", base.open_browser)),
        source=source,
    )
    return _validate(s)


def load_hitl_settings(
    path: str | os.PathLike[str] | None = None,
    *,
    raw: Mapping[str, Any] | Any | None = None,
) -> HitlSettings:
    """Return ``[hitl]`` settings; built-in defaults when no config file exists."""
    if raw is not None:
        return parse_hitl_settings(raw)
    found = find_config_path(path)
    if found is None:
        return HitlSettings()
    with found.open("rb") as fh:
        data = tomllib.load(fh)
    return parse_hitl_settings(data, source=str(found))
