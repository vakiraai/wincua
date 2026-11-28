"""Build a transport from ``[hitl]`` settings, a mode name, or an existing object."""

from __future__ import annotations

import os
from typing import Any

from wincua_hitl.local_ui import LocalUI
from wincua_hitl.settings import MODES, HitlSettings, load_hitl_settings
from wincua_hitl.terminal import CliUI
from wincua_hitl.transport import HitlTransport, OffUI, PromptAdapter


def from_settings(settings: HitlSettings | None = None, **overrides: Any) -> HitlTransport:
    s = settings or load_hitl_settings()
    if overrides:
        s = s.with_overrides(**overrides)
    common = {"timeout_s": s.timeout_s, "confirm_risky_actions": s.confirm_risky_actions}
    if s.mode == "ui":
        return LocalUI(host=s.host, port=s.port, open_browser=s.open_browser, **common)
    if s.mode == "cli":
        return CliUI(**common)
    return OffUI(**common)


def make_transport(
    spec: Any,
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> HitlTransport | None:
    """``None`` -> None; ``"auto"`` -> config; ``"ui"|"cli"|"off"`` -> that mode."""
    if spec is None:
        return None
    if isinstance(spec, HitlTransport):
        return spec
    if isinstance(spec, str):
        name = spec.lower()
        settings = load_hitl_settings(config_path)
        if name == "auto":
            return from_settings(settings)
        if name in MODES:
            return from_settings(settings, mode=name)
        raise ValueError(f"unknown hitl mode {spec!r}; expected auto, {', '.join(MODES)}")
    if hasattr(spec, "prompt"):
        return PromptAdapter(spec)
    raise TypeError(f"not a HITL transport: {spec!r}")
