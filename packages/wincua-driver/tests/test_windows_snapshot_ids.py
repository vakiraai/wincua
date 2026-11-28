"""Real WindowsBackend id resolution against the fake Win11 Calculator tree.

Regression: a human typed the snapshot id from the CURRENT listing
(``invoke num7Button_232``) and got "No element for id" because ``uia.invoke``
with a ``target`` re-bound the window, which re-walked the tree and renumbered
every snapshot id before the lookup.
"""

from __future__ import annotations

import asyncio

from test_calc_uia_tree import _FakeNode, build_win11_calculator_tree

from wincua_driver.backend.windows import WindowsBackend, find_core_window
from wincua_driver.types import Semantic


def _invokable(node: _FakeNode, log: list[str]) -> None:
    def invoke() -> None:
        log.append(node.element_info.automation_id or node.element_info.name)

    node.invoke = invoke
    node.exists = lambda timeout=0: True
    node.set_focus = lambda: None
    for child in node._children:
        _invokable(child, log)


def _backend(frame: _FakeNode) -> WindowsBackend:
    b = object.__new__(WindowsBackend)
    b.launch_timeout_s = 5.0
    b.control_timeout_s = 1.0
    b.allow = ["calculatorapp.exe"]
    b._elements, b._wrappers = {}, {}
    b._window, b._target_title = None, None
    b._id_seq = 0
    b._temp_profile_dirs = []
    b._find_window = lambda target, timeout_s=None: frame
    b._visible_titles = lambda: ["Calculator"]
    return b


def _setup():
    frame = build_win11_calculator_tree()
    pressed: list[str] = []
    _invokable(frame, pressed)
    b = _backend(frame)
    # observe() binds the CoreWindow, while _find_window("Calculator") returns
    # the outer ApplicationFrameWindow.
    core = find_core_window(frame)
    assert core is not None and core is not frame
    b._bind_window(core, title="Calculator")
    refs = [r for k, r in b._elements.items() if not k.startswith(("auto:", "name:"))]
    return b, refs, pressed


def _invoke(b: WindowsBackend, element_id: str, **extra):
    return asyncio.run(b.act(Semantic(name="uia.invoke", args={"element_id": element_id, **extra})))


def test_current_snapshot_id_resolves_with_target():
    b, refs, pressed = _setup()
    seven = next(r for r in refs if r.name == "Seven")
    assert seven.element_id.startswith("num7Button_")
    res = _invoke(b, seven.element_id, target="Calculator")
    assert res.ok, res.error
    assert pressed == ["num7Button"]


def test_snapshot_ids_survive_repeated_targeted_actions():
    b, refs, pressed = _setup()
    ids = {r.name: r.element_id for r in refs}
    for name in ("One", "Seven", "Plus", "Two", "Five", "Equals"):
        res = _invoke(b, ids[name], target="Calculator")
        assert res.ok, (name, res.error)
    assert pressed == ["num1Button", "num7Button", "plusButton", "num2Button", "num5Button", "equalButton"]


def test_stable_auto_id_from_listing_resolves():
    from wincua_hitl.transport import interpret_text
    from wincua_hitl.types import HumanRequest, observation_to_dict, suggest_actions
    from wincua_driver.types import Observation

    b, refs, pressed = _setup()
    obs = Observation(uia=refs)
    req = HumanRequest(kind="next_step", observation=observation_to_dict(obs), suggestions=suggest_actions(obs))
    seven = next(s for s in req.suggestions if s["label"] == "Seven")
    assert seven["args"]["element_id"] == "auto:num7Button"
    snapshot = next(r.element_id for r in refs if r.name == "Seven")
    for typed in ("Seven", "seven", f"invoke {snapshot}", "invoke num7Button_99999", "invoke auto:num7Button"):
        resp = interpret_text(typed, req)
        assert resp.kind == "action", typed
        res = _invoke(b, resp.action["args"]["element_id"], target="Calculator")
        assert res.ok, (typed, res.error)
    assert pressed == ["num7Button"] * 5
