"""Diagnostics when import pywinauto fails (safe on any OS)."""

from __future__ import annotations

import sys

from wincua_driver.backend.windows import format_pywinauto_import_error


def test_format_includes_executable_version_and_repr():
    exc = ModuleNotFoundError("No module named 'bogus_dep'")
    exc.name = "bogus_dep"
    msg = format_pywinauto_import_error(exc)
    assert sys.executable in msg
    assert "sys.version" in msg
    assert repr(exc) in msg or "ModuleNotFoundError" in msg
    assert "Exception chain:" in msg
    assert "Traceback:" in msg
    assert "Dependency probes:" in msg


def test_format_missing_pywinauto_suggests_pip_install():
    exc = ModuleNotFoundError("No module named 'pywinauto'")
    exc.name = "pywinauto"
    msg = format_pywinauto_import_error(exc)
    assert "python -m pip" in msg or "-m pip install" in msg
    assert "packages/wincua-driver[windows]" in msg
    assert "comtypes>=1.5.0" in msg
    assert "pywin32>=312" in msg


def test_format_dep_failure_mentions_315_pins():
    exc = ImportError("DLL load failed while importing win32api")
    msg = format_pywinauto_import_error(exc)
    assert "comtypes>=1.5.0" in msg
    assert "pywin32>=312" in msg
    assert "dependency" in msg.lower() or "pywin32" in msg.lower()
