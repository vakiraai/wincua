"""Window-state preflight: minimized / cloaked / background / off-screen / covered targets.

A fake desktop stands in for Win32 (z-order, foreground, monitors), so the real
WindowsBackend paths run on Linux against the fake Win11 Calculator tree.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from test_calc_uia_tree import _FakeNode, build_win11_calculator_tree

from wincua_driver.backend.windows import WindowsBackend, find_core_window
from wincua_driver.types import Click, Invoke, Key, Semantic, Type
from wincua_driver.window_state import (
    TARGET_OBSCURED,
    TARGET_OFFSCREEN,
    WINDOW_CLOAKED,
    WINDOW_HIDDEN,
    WINDOW_MINIMIZED,
    WINDOW_NOT_FOREGROUND,
    WINDOW_STATE_CODES,
    check_window,
)

CALC, CORE, NOTEPAD, PIN, POPUP = 0x100, 0x101, 0x200, 0x300, 0x400
CALC_RECT = (100, 100, 500, 700)


class FakeDesktop:
    """Top-level windows front-to-back; topmost windows always stay in front."""

    def __init__(self, monitors=((0, 0, 1920, 1080),)) -> None:
        self.monitors = list(monitors)
        self.win: dict[int, dict] = {}
        self.z: list[int] = []
        self.fg = 0
        self.focus_calls: list[int] = []
        self.focus_blocked = False

    def add(self, hwnd, title, rect, *, cls="", process="", parent=0, owner=0, topmost=False, **state):
        self.win[hwnd] = dict(
            title=title, rect=rect, cls=cls, process=process, parent=parent, owner=owner,
            topmost=topmost, minimized=False, visible=True, cloaked=0, **state,
        )
        if not parent:
            self.z.insert(0, hwnd)
            self._sort()
        return self

    def _sort(self) -> None:
        self.z.sort(key=lambda h: not self.win[h]["topmost"])

    def raise_(self, hwnd) -> None:
        self.z.remove(hwnd)
        self.z.insert(0, hwnd)
        self._sort()

    @staticmethod
    def _in(rect, x, y) -> bool:
        return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]

    def is_window(self, hwnd):
        return hwnd in self.win

    def root(self, hwnd):
        while self.win.get(hwnd, {}).get("parent"):
            hwnd = self.win[hwnd]["parent"]
        return hwnd

    def root_owner(self, hwnd):
        hwnd = self.root(hwnd)
        while self.win.get(hwnd, {}).get("owner"):
            hwnd = self.root(self.win[hwnd]["owner"])
        return hwnd

    def is_minimized(self, hwnd):
        return self.win[hwnd]["minimized"]

    def is_visible(self, hwnd):
        return self.win[hwnd]["visible"]

    def cloaked(self, hwnd):
        return self.win[hwnd]["cloaked"]

    def foreground(self):
        return self.fg

    def rect(self, hwnd):
        return self.win[hwnd]["rect"]

    def rect_on_screen(self, rect):
        return any(
            rect[0] < m[2] and m[0] < rect[2] and rect[1] < m[3] and m[1] < rect[3] for m in self.monitors
        )

    def point_on_screen(self, x, y):
        return any(self._in(m, x, y) for m in self.monitors)

    def window_at(self, x, y):
        for top in self.z:
            w = self.win[top]
            if w["minimized"] or not w["visible"] or w["cloaked"] or not self._in(w["rect"], x, y):
                continue
            kids = [h for h, c in self.win.items() if c["parent"] == top and self._in(c["rect"], x, y)]
            return kids[0] if kids else top
        return 0

    def title(self, hwnd):
        return self.win.get(hwnd, {}).get("title", "")

    def class_name(self, hwnd):
        return self.win.get(hwnd, {}).get("cls", "")

    def process(self, hwnd):
        return self.win.get(hwnd, {}).get("process", "")

    def restore_and_focus(self, hwnd):
        self.focus_calls.append(hwnd)
        if self.focus_blocked:
            return False
        self.win[hwnd]["minimized"] = False
        self.raise_(hwnd)
        self.fg = hwnd
        return True


def calculator_desktop() -> FakeDesktop:
    d = FakeDesktop()
    d.add(CALC, "Calculator", CALC_RECT, cls="ApplicationFrameWindow", process="ApplicationFrameHost.exe")
    d.add(CORE, "Calculator", (100, 132, 500, 700), cls="Windows.UI.Core.CoreWindow",
          process="CalculatorApp.exe", parent=CALC)
    d.fg = CALC
    return d


def cover_with_notepad(d: FakeDesktop, *, topmost=False, foreground=True) -> None:
    d.add(NOTEPAD, "Untitled - Notepad", (50, 50, 900, 900), cls="Notepad", process="notepad.exe", topmost=topmost)
    if foreground:
        d.fg = NOTEPAD


# --- check_window (pure) ---------------------------------------------------


def test_all_codes_have_hints():
    from wincua_driver.window_state import HINTS

    assert set(HINTS) == WINDOW_STATE_CODES


@pytest.mark.parametrize(
    "setup, code",
    [
        (lambda d: d.win[CALC].update(minimized=True), WINDOW_MINIMIZED),
        (lambda d: d.win[CALC].update(cloaked=2), WINDOW_CLOAKED),
        (lambda d: d.win[CALC].update(visible=False), WINDOW_HIDDEN),
        (lambda d: d.win[CALC].update(rect=(3000, 100, 3400, 700)), TARGET_OFFSCREEN),
    ],
)
def test_window_level_codes(setup, code):
    d = calculator_desktop()
    setup(d)
    err = check_window(d, CALC, need="pattern")
    assert err is not None and err.code == code
    assert err.details["title"] == "Calculator"
    assert err.hint


def test_covered_point_is_obscured_and_names_the_covering_window():
    d = calculator_desktop()
    cover_with_notepad(d)
    err = check_window(d, CALC, need="pointer", point=(300, 400))
    assert err.code == TARGET_OBSCURED
    assert err.details["covering"]["title"] == "Untitled - Notepad"
    assert err.details["covering"]["process"] == "notepad.exe"
    assert "Notepad" in err.message


def test_child_windows_and_owned_popups_are_not_obscuring():
    d = calculator_desktop()
    assert check_window(d, CALC, need="pointer", point=(300, 400)) is None  # hits the CoreWindow child
    d.add(POPUP, "", (250, 350, 350, 450), cls="Popup", owner=CALC)
    assert d.window_at(300, 400) == POPUP
    assert check_window(d, CALC, need="pointer", point=(300, 400)) is None


def test_background_window_fails_keyboard_but_not_pattern_actions():
    d = calculator_desktop()
    cover_with_notepad(d)
    assert check_window(d, CALC, need="pattern") is None
    err = check_window(d, CALC, need="keyboard")
    assert err.code == WINDOW_NOT_FOREGROUND
    assert err.details["foreground"]["title"] == "Untitled - Notepad"


def test_point_off_monitor_or_outside_window_is_offscreen():
    d = calculator_desktop()
    d.win[CALC]["rect"] = (1700, 100, 2100, 700)  # half off the right edge
    err = check_window(d, CALC, need="pointer", point=(2000, 400))
    assert (err.code, err.details["scope"]) == (TARGET_OFFSCREEN, "point")
    err = check_window(d, CALC, need="pointer", point=(1800, 900))
    assert (err.code, err.details["scope"]) == (TARGET_OFFSCREEN, "outside_window")


# --- WindowsBackend preflight ------------------------------------------------


def _rect(x, y, w=40, h=30):
    return SimpleNamespace(left=x, top=y, right=x + w, bottom=y + h)


def _wire(node: _FakeNode, log: list[str], pos: list[int]) -> None:
    aid = node.element_info.automation_id or node.element_info.name

    def invoke():
        if getattr(node, "invoke_raises", False):
            raise RuntimeError("Invoke pattern not supported")
        log.append(f"invoke:{aid}")

    node.invoke = invoke
    node.click_input = lambda: log.append(f"click:{aid}")
    node.exists = lambda timeout=0: True
    node.set_focus = lambda: None
    if node.element_info.control_type == "Button":
        pos[0] += 1
        r = _rect(120 + 50 * (pos[0] % 6), 400 + 40 * (pos[0] // 6))
        node.rectangle = lambda: r
    for child in node._children:
        _wire(child, log, pos)


def _backend(desktop: FakeDesktop, *, auto_focus=True):
    frame = build_win11_calculator_tree()
    frame.handle = CALC
    log: list[str] = []
    _wire(frame, log, [0])
    core = find_core_window(frame)
    core.handle = CORE
    b = object.__new__(WindowsBackend)
    b.launch_timeout_s, b.control_timeout_s = 5.0, 1.0
    b.allow = ["calculatorapp.exe"]
    b._elements, b._wrappers = {}, {}
    b._window, b._target_title = None, None
    b._id_seq = 0
    b._temp_profile_dirs = []
    b._find_window = lambda target, timeout_s=None: frame
    b._calculator_frame = lambda seed=None: frame
    b._visible_titles = lambda: ["Calculator"]
    b._probe = desktop
    b.auto_focus = auto_focus
    b.focus_settle_s = 0
    b._bind_window(core, title="Calculator")
    return b, log


def _act(b, action):
    return asyncio.run(b.act(action))


def test_invoke_works_on_a_covered_background_window_without_stealing_focus():
    d = calculator_desktop()
    cover_with_notepad(d)
    b, log = _backend(d)
    res = _act(b, Invoke("auto:num7Button"))
    assert res.ok, res.error
    assert log == ["invoke:num7Button"]
    assert d.focus_calls == [] and d.fg == NOTEPAD
    assert not (res.info or {}).get("window_recovered")


def test_minimized_window_is_restored_once_and_reported():
    d = calculator_desktop()
    d.win[CALC]["minimized"] = True
    b, log = _backend(d)
    res = _act(b, Invoke("auto:num7Button"))
    assert res.ok, res.error
    assert log == ["invoke:num7Button"]
    assert d.focus_calls == [CALC]
    assert res.info["window_recovered"] == [WINDOW_MINIMIZED]


def test_minimized_window_errors_when_auto_focus_is_off():
    d = calculator_desktop()
    d.win[CALC]["minimized"] = True
    b, log = _backend(d, auto_focus=False)
    res = _act(b, Semantic("uia.invoke", {"auto_id": "num7Button", "target": "Calculator"}))
    assert not res.ok
    assert res.error.code == WINDOW_MINIMIZED
    assert res.error.details["title"] == "Calculator"
    assert res.error.details["auto_focus"] is False
    assert res.error.hint
    assert log == [] and d.focus_calls == []


def test_click_on_covered_window_is_refused_not_misdelivered():
    d = calculator_desktop()
    cover_with_notepad(d)
    b, log = _backend(d, auto_focus=False)
    res = _act(b, Click(element_id="auto:num7Button"))
    assert not res.ok
    assert res.error.code == TARGET_OBSCURED
    assert res.error.details["covering"]["title"] == "Untitled - Notepad"
    assert res.error.details["element_id"] == "auto:num7Button"
    assert log == []


def test_click_on_covered_window_recovers_after_focus():
    d = calculator_desktop()
    cover_with_notepad(d)
    b, log = _backend(d)
    res = _act(b, Click(element_id="auto:num7Button"))
    assert res.ok, res.error
    assert log == ["click:num7Button"]
    assert d.focus_calls == [CALC]
    assert res.info["window_recovered"] == [TARGET_OBSCURED]


def test_always_on_top_cover_fails_after_one_focus_attempt():
    d = calculator_desktop()
    cover_with_notepad(d, topmost=True)
    b, log = _backend(d)
    res = _act(b, Click(element_id="auto:num7Button"))
    assert not res.ok
    assert res.error.code == TARGET_OBSCURED
    assert res.error.details["auto_focus"] is True
    assert res.error.details["first_error"] == TARGET_OBSCURED
    assert "Tried restore/focus once" in res.error.message
    assert d.focus_calls == [CALC]
    assert log == []


def test_coordinate_click_checks_the_point():
    d = calculator_desktop()
    cover_with_notepad(d, topmost=True)
    b, _ = _backend(d, auto_focus=False)
    res = _act(b, Click(x=300, y=400))
    assert res.error.code == TARGET_OBSCURED
    res = _act(b, Click(x=2500, y=400))
    assert res.error.code == TARGET_OFFSCREEN


def test_keys_are_not_sent_to_another_window():
    d = calculator_desktop()
    cover_with_notepad(d)
    d.focus_blocked = True
    b, _ = _backend(d)
    for action in (Key("enter"), Type(text="17")):
        res = _act(b, action)
        assert res.error.code == WINDOW_NOT_FOREGROUND, action
        assert res.error.details["foreground"]["title"] == "Untitled - Notepad"
    assert d.focus_calls == [CALC, CALC]


def test_invoke_click_fallback_is_skipped_when_covered():
    d = calculator_desktop()
    cover_with_notepad(d, topmost=True)
    b, log = _backend(d)
    seven = b._resolve_wrapper("auto:num7Button")
    seven.invoke_raises = True
    res = _act(b, Invoke("auto:num7Button"))
    assert res.error.code == TARGET_OBSCURED
    assert "not falling back to a click" in res.error.message
    assert "invoke_error" in res.error.details
    assert log == []


def test_cloaked_window_errors_after_one_try():
    d = calculator_desktop()
    d.win[CALC]["cloaked"] = 2
    b, log = _backend(d)
    res = _act(b, Invoke("auto:num7Button"))
    assert res.error.code == WINDOW_CLOAKED
    assert d.focus_calls == [CALC]
    assert log == []


def test_detached_core_window_maps_to_the_frame():
    d = calculator_desktop()
    d.win[CALC]["minimized"] = True
    d.win[CORE].update(parent=0)  # minimized UWP: CoreWindow leaves the frame
    b, _ = _backend(d, auto_focus=False)
    err = b._window_state_error("observe")
    assert err.code == WINDOW_MINIMIZED and err.details["hwnd"] == CALC


def test_focus_fails_when_window_stays_in_background():
    d = calculator_desktop()
    cover_with_notepad(d)
    d.focus_blocked = True
    b, _ = _backend(d, auto_focus=False)
    res = b._focus_result()
    assert res.error.code == WINDOW_NOT_FOREGROUND
    assert d.focus_calls == [CALC]  # focus retries even with auto_focus off
    d.focus_blocked = False
    assert b._focus_result().ok


def test_no_probe_off_windows_skips_the_preflight():
    b, log = _backend(calculator_desktop())
    b._probe = None
    res = _act(b, Invoke("auto:num7Button"))
    assert res.ok and log == ["invoke:num7Button"]


def test_auto_focus_env_switch(monkeypatch):
    import wincua_driver.backend.windows as win

    monkeypatch.setattr(win, "is_windows", lambda: True)
    monkeypatch.setattr(win, "ensure_pywinauto", lambda: None)
    monkeypatch.setenv("WINCUA_AUTO_FOCUS", "0")
    assert win.WindowsBackend(allow=["calc.exe"]).auto_focus is False
    monkeypatch.delenv("WINCUA_AUTO_FOCUS")
    assert win.WindowsBackend(allow=["calc.exe"]).auto_focus is True
    assert win.WindowsBackend(allow=["calc.exe"], auto_focus=False).auto_focus is False
