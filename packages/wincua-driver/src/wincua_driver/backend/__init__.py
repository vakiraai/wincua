"""Pluggable driver backends (null/mock for CI; cua/native later)."""

from __future__ import annotations

from wincua_driver.backend.base import DriverBackend
from wincua_driver.backend.mock import MockBackend
from wincua_driver.backend.null import NullBackend

_current: DriverBackend | None = None


def get_backend() -> DriverBackend:
    global _current
    if _current is None:
        _current = NullBackend()
    return _current


def set_backend(backend: DriverBackend | str) -> DriverBackend:
    """Set active backend. Pass instance or name: 'null' | 'mock'."""
    global _current
    if isinstance(backend, str):
        name = backend.lower()
        if name == "null":
            _current = NullBackend()
        elif name == "mock":
            _current = MockBackend()
        else:
            raise ValueError(f"Unknown backend: {backend!r} (stub supports null|mock)")
    else:
        _current = backend
    return _current


__all__ = [
    "DriverBackend",
    "MockBackend",
    "NullBackend",
    "get_backend",
    "set_backend",
]
