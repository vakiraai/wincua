"""Shared Win11 demo preflight — fail fast with full pywinauto diagnostics."""

from __future__ import annotations

import sys


def require_windows_uia_stack() -> int:
    """Return 0 if pywinauto imports; else print diagnostics and return 1."""
    from wincua_driver.backend.windows import (
        _SUPPORTED_WINDOWS_PYTHON,
        probe_pywinauto,
    )

    print(f"  python: {sys.version.split()[0]}  executable: {sys.executable}")
    if sys.version_info >= (3, 15):
        print(
            f"WARNING: CPython {sys.version_info.major}.{sys.version_info.minor} "
            f"is outside the supported Windows host range "
            f"({_SUPPORTED_WINDOWS_PYTHON}). "
            "`import pywinauto` often fails via pywin32/comtypes even when "
            "pip show pywinauto succeeds. Prefer a 3.11–3.13 venv.",
            file=sys.stderr,
        )

    ok, detail = probe_pywinauto()
    if ok:
        print(f"  uia_deps: {detail}")
        return 0
    print(detail, file=sys.stderr)
    return 1
