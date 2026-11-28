"""Allow-list: a bare process name ("msedge", as a model or MCP client writes it) matches "msedge.exe"."""

from __future__ import annotations

import pytest

from wincua_driver.backend.windows import WindowsBackend


def _backend(allow):
    b = object.__new__(WindowsBackend)
    b.allow = list(allow)
    return b


@pytest.mark.parametrize("name", ["msedge", "MSEdge", "msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"])
def test_edge_is_allowed_however_it_is_named(name):
    assert _backend(["msedge.exe", "chrome.exe"])._check_process_allowed(name) is None


@pytest.mark.parametrize("name", ["notepad", "notepad.exe", "msedge.bat", "msedgewebview2"])
def test_other_processes_stay_denied(name):
    err = _backend(["msedge.exe", "chrome.exe"])._check_process_allowed(name)
    assert err is not None and err.code == "allow_list_denied"


def test_calculator_aliases_unchanged():
    assert _backend(["CalculatorApp.exe"])._check_process_allowed("calculator") is None
    assert _backend(["msedge.exe"])._check_process_allowed("calculator").code == "allow_list_denied"
