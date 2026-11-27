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
    print(f"  host_python_support: {_SUPPORTED_WINDOWS_PYTHON}")
    if sys.version_info >= (3, 15):
        print(
            "  note: Python 3.15 needs comtypes>=1.5.0 and pywin32>=312 "
            "(cp315 win_amd64 wheels). Reinstall windows extras if import fails."
        )

    ok, detail = probe_pywinauto()
    if ok:
        print(f"  uia_deps: {detail}")
        return 0
    print(detail, file=sys.stderr)
    return 1
