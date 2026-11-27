"""Pluggable driver backends (null/mock for CI; windows via pywinauto/UIA)."""

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


def set_backend(backend: DriverBackend | str, *, allow: list[str] | None = None) -> DriverBackend:
    """Set active backend. Pass instance or name: 'null' | 'mock' | 'windows'."""
    global _current
    if isinstance(backend, str):
        name = backend.lower()
        if name == "null":
            _current = NullBackend()
        elif name == "mock":
            _current = MockBackend()
        elif name in ("windows", "uia", "pywinauto"):
            from wincua_driver.backend.windows import WindowsBackend, is_windows, windows_unavailable_error

            if not is_windows():
                raise RuntimeError(windows_unavailable_error().message)
            _current = WindowsBackend(allow=allow)
        else:
            raise ValueError(
                f"Unknown backend: {backend!r} (supported: null|mock|windows)"
            )
    else:
        _current = backend
        if allow is not None and hasattr(_current, "set_allow"):
            _current.set_allow(allow)  # type: ignore[attr-defined]
    return _current


__all__ = [
    "DriverBackend",
    "MockBackend",
    "NullBackend",
    "get_backend",
    "set_backend",
]
