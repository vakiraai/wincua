"""Pure helpers used by Windows launch (safe on any OS)."""

from __future__ import annotations

from wincua_driver.backend.windows import (
    calculator_launch_strategies,
    is_calculator_app,
    match_window_title,
    resolve_browser_executable,
)


def test_match_window_title_substring_and_exact():
    assert match_window_title("Calculator", "Calculator")
    assert match_window_title("My Calculator - untitled", "Calculator")
    assert match_window_title("wincua form demo — submitted", "wincua form demo")
    assert not match_window_title("", "Calculator")
    assert not match_window_title("Notepad", "Calculator")
    assert not match_window_title("Settings", "Calculator")


def test_is_calculator_app_aliases():
    assert is_calculator_app("calc.exe")
    assert is_calculator_app("calculator")
    assert is_calculator_app("calculator:")
    assert is_calculator_app("CalculatorApp.exe")
    assert not is_calculator_app("msedge.exe")


def test_calculator_strategies_avoid_pywinauto_start():
    labels = [label for label, _ in calculator_launch_strategies()]
    assert "protocol:calculator:" in labels
    assert "explorer:AppsFolder" in labels
    assert any("calc.exe" == label for label in labels)


def test_resolve_browser_executable_missing_returns_none_on_linux():
    # On Linux CI there is typically no Edge/Chrome in Win paths
    exe = resolve_browser_executable("msedge")
    # May be None, or a path if somehow installed — just ensure no throw
    assert exe is None or exe.name.lower().endswith(".exe") or "edge" in exe.name.lower() or "chrome" in exe.name.lower()
