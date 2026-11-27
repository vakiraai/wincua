"""Element-id resolution on the Win11 Calculator tree (no pywinauto / desktop needed).

Regression: a model passed element_id='clearButton' (bare AutomationId) and stale
snapshot ids like 'clearButton_112'; both must resolve to the Clear button that
sits under CoreWindow > NavView > LandmarkTarget > DisplayControls.
"""

from __future__ import annotations

from test_calc_uia_tree import build_win11_calculator_tree
from wincua_driver.backend.windows import WindowsBackend


def make_backend(frame) -> WindowsBackend:
    wb = object.__new__(WindowsBackend)
    wb.allow = ["calculatorapp.exe"]
    wb.launch_timeout_s = 1.0
    wb.control_timeout_s = 0.3
    wb._wrappers = {}
    wb._elements = {}
    wb._id_seq = 100
    wb._window = frame
    wb._target_title = "Calculator"
    wb._search_roots = lambda: [frame]
    return wb


def _auto(node) -> str:
    return node.element_info.automation_id


def test_bare_and_stale_ids_resolve_by_automation_id():
    frame = build_win11_calculator_tree()
    wb = make_backend(frame)
    assert _auto(wb._resolve_wrapper("clearButton")) == "clearButton"
    assert _auto(wb._resolve_wrapper("clearButton_112")) == "clearButton"
    assert _auto(wb._resolve_wrapper("auto:num7Button")) == "num7Button"
    assert wb._resolve_wrapper("percentButton") is None


def test_walk_records_auto_id_and_snapshot_ids_change():
    frame = build_win11_calculator_tree()
    wb = make_backend(frame)
    first = {r.auto_id: r.element_id for r in wb._walk_uia(frame) if r.auto_id}
    second = {r.auto_id: r.element_id for r in wb._walk_uia(frame) if r.auto_id}
    assert {"clearButton", "num1Button", "CalculatorResults"} <= set(first)
    assert first["clearButton"] != second["clearButton"]
    # A snapshot id from the previous walk still resolves.
    assert _auto(wb._resolve_wrapper(first["clearButton"])) == "clearButton"
