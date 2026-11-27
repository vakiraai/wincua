"""UIA tree dump helpers (no pywinauto required)."""

from __future__ import annotations

from wincua_driver.backend.windows import (
    _CALC_NAME_FALLBACKS,
    describe_uia_element,
    format_uia_tree,
)


class _FakeInfo:
    def __init__(self, name="", control_type="", automation_id="", class_name="", process_id=0):
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

    def descendants(self, depth: int | None = None):
        out: list[_FakeNode] = []

        def walk(n: _FakeNode, d: int) -> None:
            if depth is not None and d > depth:
                return
            for c in n._children:
                out.append(c)
                walk(c, d + 1)

        walk(self, 1)
        return out


def test_describe_uia_element_fields():
    node = _FakeNode(
        _FakeInfo(
            name="One",
            control_type="Button",
            automation_id="num1Button",
            class_name="Button",
            process_id=4242,
        )
    )
    meta = describe_uia_element(node)
    assert meta["name"] == "One"
    assert meta["control_type"] == "Button"
    assert meta["auto_id"] == "num1Button"
    assert meta["pid"] == 4242


def test_format_uia_tree_depth_and_fields():
    leaf = _FakeNode(
        _FakeInfo(name="One", control_type="Button", automation_id="num1Button")
    )
    root = _FakeNode(
        _FakeInfo(name="Calculator", control_type="Window", class_name="ApplicationFrameWindow"),
        children=[leaf],
    )
    text = format_uia_tree(root, max_depth=6, max_nodes=50)
    assert "Calculator" in text
    assert "num1Button" in text
    assert "One" in text
    assert "[Button]" in text


def test_name_fallbacks_cover_requested_labels():
    assert "One" in _CALC_NAME_FALLBACKS["num1Button"]
    assert "Plus" in _CALC_NAME_FALLBACKS["plusButton"]
    assert "Equals" in _CALC_NAME_FALLBACKS["equalButton"]
    assert "Clear" in _CALC_NAME_FALLBACKS["clearButton"]
    assert any(n.startswith("Display is") or n == "Display is" for n in _CALC_NAME_FALLBACKS["CalculatorResults"])
