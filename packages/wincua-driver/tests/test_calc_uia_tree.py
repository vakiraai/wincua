"""Fake Win11 Calculator UIA tree — shaped like the live dump (27 Nov 2026).

ApplicationFrameWindow (ApplicationFrameHost)
  └─ Windows.UI.Core.CoreWindow (CalculatorApp.exe)
       └─ NavView
            └─ LandmarkTarget
                 ├─ CalculatorResults / DisplayControls / StandardOperators / NumberPad
"""

from __future__ import annotations

from wincua_driver.backend.windows import (
    find_core_window,
    find_uia_by_auto_id,
    find_uia_by_name,
    format_uia_tree,
    is_calculator_frame,
    iter_uia_tree,
    pick_calculator_frame,
    window_class_name,
)


class _FakeInfo:
    def __init__(
        self,
        name="",
        control_type="",
        automation_id="",
        class_name="",
        process_id=0,
    ):
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id
        self.class_name = class_name
        self.process_id = process_id


class _FakeNode:
    def __init__(self, info: _FakeInfo, children: list["_FakeNode"] | None = None):
        self.element_info = info
        self._children = children or []

    def window_text(self) -> str:
        return self.element_info.name

    def process_id(self) -> int:
        return self.element_info.process_id

    def children(self):
        return list(self._children)

    def child_window(self, **kwargs):
        """Minimal stub — exists() always false so lookup uses the children() walk."""

        class _Spec:
            def exists(self, timeout=0):
                return False

        return _Spec()

    def descendants(self, depth: int | None = None, auto_id: str | None = None):
        out: list[_FakeNode] = []

        def walk(n: _FakeNode, d: int) -> None:
            if depth is not None and d > depth:
                return
            for c in n._children:
                if auto_id is None or (
                    getattr(c.element_info, "automation_id", None) or ""
                ) == auto_id:
                    out.append(c)
                walk(c, d + 1)

        walk(self, 1)
        return out


def _btn(auto_id: str, name: str) -> _FakeNode:
    return _FakeNode(
        _FakeInfo(
            name=name,
            control_type="Button",
            automation_id=auto_id,
            class_name="Button",
            process_id=9844,
        )
    )


def build_win11_calculator_tree() -> _FakeNode:
    """~5 levels deep under ApplicationFrameWindow — mirrors the user dump."""
    number_pad = _FakeNode(
        _FakeInfo(name="Number pad", control_type="Group", automation_id="NumberPad"),
        children=[
            _btn("num0Button", "Zero"),
            _btn("num1Button", "One"),
            _btn("num2Button", "Two"),
            _btn("num5Button", "Five"),
            _btn("num7Button", "Seven"),
        ],
    )
    operators = _FakeNode(
        _FakeInfo(
            name="Standard operators",
            control_type="Group",
            automation_id="StandardOperators",
        ),
        children=[
            _btn("plusButton", "Plus"),
            _btn("equalButton", "Equals"),
        ],
    )
    display_controls = _FakeNode(
        _FakeInfo(
            name="Display controls",
            control_type="Group",
            automation_id="DisplayControls",
        ),
        children=[
            _btn("clearEntryButton", "Clear entry"),
            _btn("clearButton", "Clear"),
        ],
    )
    results = _FakeNode(
        _FakeInfo(
            name="Display is 0",
            control_type="Text",
            automation_id="CalculatorResults",
            class_name="TextBlock",
            process_id=9844,
        )
    )
    expression = _FakeNode(
        _FakeInfo(
            name="",
            control_type="Text",
            automation_id="CalculatorExpression",
            process_id=9844,
        )
    )
    landmark = _FakeNode(
        _FakeInfo(name="", control_type="Group", class_name="LandmarkTarget"),
        children=[expression, results, display_controls, operators, number_pad],
    )
    nav = _FakeNode(
        _FakeInfo(name="Navigation", control_type="Custom", automation_id="NavView"),
        children=[landmark],
    )
    core = _FakeNode(
        _FakeInfo(
            name="Calculator",
            control_type="Window",
            class_name="Windows.UI.Core.CoreWindow",
            process_id=9844,
        ),
        children=[nav],
    )
    frame = _FakeNode(
        _FakeInfo(
            name="Calculator",
            control_type="Window",
            class_name="ApplicationFrameWindow",
            process_id=9132,
        ),
        children=[core],
    )
    return frame


def test_is_calculator_frame_requires_afw_and_exact_title():
    frame = build_win11_calculator_tree()
    assert is_calculator_frame(frame)
    chrome = _FakeNode(
        _FakeInfo(
            name="Windows UIA driver + live Calculator/browser demos · Pull Request #1",
            control_type="Window",
            class_name="Chrome_WidgetWin_1",
        )
    )
    assert not is_calculator_frame(chrome)
    wrong_title = _FakeNode(
        _FakeInfo(
            name="My Calculator",
            control_type="Window",
            class_name="ApplicationFrameWindow",
        )
    )
    assert not is_calculator_frame(wrong_title)


def test_pick_calculator_frame_skips_chrome_substring_match():
    frame = build_win11_calculator_tree()
    chrome = _FakeNode(
        _FakeInfo(
            name="Something about Calculator in the tab title",
            control_type="Window",
            class_name="Chrome_WidgetWin_1",
        )
    )
    picked = pick_calculator_frame([chrome, frame])
    assert picked is frame
    assert window_class_name(picked) == "ApplicationFrameWindow"


def test_find_core_window_under_frame():
    frame = build_win11_calculator_tree()
    core = find_core_window(frame)
    assert core is not None
    assert window_class_name(core) == "Windows.UI.Core.CoreWindow"


def test_shared_walk_reaches_depth_5_controls_from_frame():
    frame = build_win11_calculator_tree()
    nodes = list(iter_uia_tree(frame, max_depth=None, max_nodes=None))
    auto_ids = {
        getattr(n.element_info, "automation_id", None) for n, _ in nodes
    }
    assert "num1Button" in auto_ids
    assert "clearButton" in auto_ids
    assert "plusButton" in auto_ids
    assert "equalButton" in auto_ids
    assert "CalculatorResults" in auto_ids
    # Depth of num1Button: frame/core/nav/landmark/numberpad/btn = 5
    depths = {aid: d for (n, d) in nodes if (aid := getattr(n.element_info, "automation_id", None))}
    assert depths["num1Button"] == 5


def test_find_uia_by_auto_id_from_frame_and_core():
    frame = build_win11_calculator_tree()
    core = find_core_window(frame)
    assert find_uia_by_auto_id(frame, "num1Button") is not None
    assert find_uia_by_auto_id(core, "clearButton") is not None
    assert find_uia_by_auto_id(frame, "equalButton", control_type="Button") is not None
    results = find_uia_by_auto_id(frame, "CalculatorResults")
    assert results is not None
    assert results.element_info.name.startswith("Display is")


def test_find_uia_by_name_fallbacks():
    frame = build_win11_calculator_tree()
    assert find_uia_by_name(frame, ("One",), control_type="Button") is not None
    assert find_uia_by_name(frame, ("Clear",), control_type="Button") is not None
    assert find_uia_by_name(frame, ("Display is",), prefix=True) is not None


def test_dump_tree_shows_standard_auto_ids():
    text = format_uia_tree(build_win11_calculator_tree(), max_depth=6, max_nodes=200)
    assert "ApplicationFrameWindow" in text
    assert "Windows.UI.Core.CoreWindow" in text
    assert "num1Button" in text
    assert "clearButton" in text
    assert "CalculatorResults" in text
