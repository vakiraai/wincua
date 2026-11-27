"""Windows backend selection + allow-list guards (safe on Linux CI)."""

from __future__ import annotations

import platform

import pytest


def test_is_windows_matches_platform():
    from wincua_driver.backend.windows import is_windows

    assert is_windows() is (platform.system() == "Windows")


def test_set_backend_windows_fails_clearly_off_windows():
    if platform.system() == "Windows":
        pytest.skip("Windows host — instantiation should succeed")
    from wincua_driver.backend import set_backend

    with pytest.raises(RuntimeError, match="requires an interactive Windows"):
        set_backend("windows", allow=["calc.exe"])


def test_windows_backend_import_does_not_require_pywinauto_off_windows():
    """Module import must stay light for CI; pywinauto only needed on construct."""
    from wincua_driver.backend import windows as win_mod

    assert hasattr(win_mod, "WindowsBackend")
    assert hasattr(win_mod, "is_windows")
    if platform.system() != "Windows":
        with pytest.raises(RuntimeError, match="requires an interactive Windows"):
            win_mod.WindowsBackend(allow=["msedge.exe"])
