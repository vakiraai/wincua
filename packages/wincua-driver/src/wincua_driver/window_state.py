"""Window-state preflight: refuse to act on a minimized, hidden, background or covered window.

UIA patterns (Invoke, Value) reach a window wherever it is, but synthesized
input does not: ``send_keys`` goes to the foreground window and a mouse click
lands on whatever is on top at that point. Before acting, the driver checks the
target's top-level window and returns one of these codes instead of typing or
clicking into the wrong window.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from wincua_driver.types import StructuredError

WINDOW_MINIMIZED = "window_minimized"
WINDOW_HIDDEN = "window_hidden"
WINDOW_CLOAKED = "window_cloaked"
WINDOW_NOT_FOREGROUND = "window_not_foreground"
TARGET_OFFSCREEN = "target_offscreen"
TARGET_OBSCURED = "target_obscured"

WINDOW_STATE_CODES = frozenset(
    {
        WINDOW_MINIMIZED,
        WINDOW_HIDDEN,
        WINDOW_CLOAKED,
        WINDOW_NOT_FOREGROUND,
        TARGET_OFFSCREEN,
        TARGET_OBSCURED,
    }
)

# What an action needs from its window:
#   pattern  - UIA pattern call (Invoke/Value): window shown and on screen
#   keyboard - send_keys: also the foreground window
#   pointer  - mouse at a point: also the point on screen and not covered
Need = Literal["observe", "pattern", "keyboard", "pointer"]

HINTS: dict[str, str] = {
    WINDOW_MINIMIZED: "Call focus with the window title to restore it (or ask the human to restore it), observe again, then retry.",
    WINDOW_HIDDEN: "The window is hidden (e.g. closed to the tray). Ask the human to open it, or launch the app again.",
    WINDOW_CLOAKED: (
        "Windows is hiding this window (another virtual desktop, or a suspended app). "
        "Ask the human to switch to it, then focus and observe again."
    ),
    WINDOW_NOT_FOREGROUND: (
        "Call focus with the window title, then retry. If focus keeps failing (Windows blocks focus "
        "changes from background processes), ask the human to click the window."
    ),
    TARGET_OFFSCREEN: (
        "Move the window onto a visible monitor or scroll the element into view, observe again for "
        "fresh bounds, then retry. Prefer invoke over click for buttons."
    ),
    TARGET_OBSCURED: (
        "Another window covers the click point. Call focus on the target and retry, prefer invoke over "
        "click, or ask the human to move or close the covering window (always-on-top windows stay in front)."
    ),
}

_CLOAK_REASONS = {1: "app", 2: "shell (another virtual desktop)", 4: "inherited from owner"}


class WindowProbe(Protocol):
    """Win32 queries the preflight needs; tests substitute a fake desktop."""

    def is_window(self, hwnd: int) -> bool: ...
    def root(self, hwnd: int) -> int: ...
    def root_owner(self, hwnd: int) -> int: ...
    def is_minimized(self, hwnd: int) -> bool: ...
    def is_visible(self, hwnd: int) -> bool: ...
    def cloaked(self, hwnd: int) -> int: ...
    def foreground(self) -> int: ...
    def rect(self, hwnd: int) -> tuple[int, int, int, int] | None: ...
    def rect_on_screen(self, rect: tuple[int, int, int, int]) -> bool: ...
    def point_on_screen(self, x: int, y: int) -> bool: ...
    def window_at(self, x: int, y: int) -> int: ...
    def title(self, hwnd: int) -> str: ...
    def class_name(self, hwnd: int) -> str: ...
    def process(self, hwnd: int) -> str: ...
    def restore_and_focus(self, hwnd: int) -> bool: ...


def window_error(code: str, message: str, details: dict[str, Any]) -> StructuredError:
    return StructuredError(code=code, message=message, details=details, hint=HINTS.get(code, ""))


def is_window_state_error(err: StructuredError | None) -> bool:
    return err is not None and err.code in WINDOW_STATE_CODES


def _describe(probe: WindowProbe, hwnd: int) -> dict[str, Any]:
    return {
        "hwnd": hwnd,
        "title": probe.title(hwnd) if hwnd else "",
        "process": probe.process(hwnd) if hwnd else "",
        "class_name": probe.class_name(hwnd) if hwnd else "",
    }


def belongs_to(probe: WindowProbe, hwnd: int, target: int) -> bool:
    """True if ``hwnd`` is ``target``, one of its child windows, or a popup/dialog it owns."""
    if not hwnd:
        return False
    if hwnd == target:
        return True
    root = probe.root(hwnd)
    return root == target or probe.root_owner(root or hwnd) == target


def _inside(rect: tuple[int, int, int, int], point: tuple[int, int]) -> bool:
    left, top, right, bottom = rect
    return left <= point[0] < right and top <= point[1] < bottom


def check_window(
    probe: WindowProbe,
    hwnd: int,
    *,
    need: Need = "pattern",
    point: tuple[int, int] | None = None,
    title: str | None = None,
) -> StructuredError | None:
    """First reason the top-level window ``hwnd`` cannot take a ``need`` action, else None."""
    title = title or probe.title(hwnd)
    details: dict[str, Any] = {"title": title, "hwnd": hwnd, "need": need}
    if point is not None:
        details["point"] = [int(point[0]), int(point[1])]

    if probe.is_minimized(hwnd):
        return window_error(WINDOW_MINIMIZED, f"Window {title!r} is minimized.", details)
    cloak = probe.cloaked(hwnd)
    if cloak:
        reason = _CLOAK_REASONS.get(cloak, str(cloak))
        return window_error(
            WINDOW_CLOAKED,
            f"Window {title!r} is cloaked by DWM ({reason}); it is not visible on this desktop.",
            {**details, "cloaked": cloak},
        )
    if not probe.is_visible(hwnd):
        return window_error(WINDOW_HIDDEN, f"Window {title!r} is not visible.", details)
    rect = probe.rect(hwnd)
    if rect is not None:
        details["window_rect"] = list(rect)
        if not probe.rect_on_screen(rect):
            return window_error(
                TARGET_OFFSCREEN,
                f"Window {title!r} at {list(rect)} is not on any monitor.",
                {**details, "scope": "window"},
            )

    if point is not None:
        if not probe.point_on_screen(*point):
            return window_error(
                TARGET_OFFSCREEN,
                f"Click point {list(point)} in {title!r} is not on any monitor.",
                {**details, "scope": "point"},
            )
        if rect is not None and not _inside(rect, point):
            return window_error(
                TARGET_OFFSCREEN,
                f"Click point {list(point)} is outside window {title!r} {list(rect)} "
                "(element scrolled out of view, or stale bounds).",
                {**details, "scope": "outside_window"},
            )
        hit = probe.window_at(*point)
        if hit and not belongs_to(probe, hit, hwnd):
            cover = _describe(probe, probe.root(hit) or hit)
            return window_error(
                TARGET_OBSCURED,
                f"Click point {list(point)} in {title!r} is covered by "
                f"{cover['title'] or cover['class_name']!r} ({cover['process'] or '?'}).",
                {**details, "covering": cover},
            )

    if need in ("keyboard", "pointer"):
        fg = probe.foreground()
        if not belongs_to(probe, fg, hwnd):
            front = _describe(probe, fg)
            return window_error(
                WINDOW_NOT_FOREGROUND,
                f"Window {title!r} is not in the foreground "
                f"({front['title'] or front['class_name'] or 'nothing'!r} is).",
                {**details, "foreground": front},
            )
    return None


class Win32WindowProbe:
    """ctypes user32/dwmapi implementation of :class:`WindowProbe` (Windows only)."""

    GA_ROOT = 2
    GA_ROOTOWNER = 3
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    DWMWA_CLOAKED = 14
    MONITOR_DEFAULTTONULL = 0
    SW_SHOW = 5
    SW_RESTORE = 9
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x2

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wt = wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        HWND, UINT, DWORD, BOOL = wintypes.HWND, wintypes.UINT, wintypes.DWORD, wintypes.BOOL
        for name, args, res in (
            ("IsWindow", [HWND], BOOL),
            ("GetAncestor", [HWND, UINT], HWND),
            ("IsIconic", [HWND], BOOL),
            ("IsWindowVisible", [HWND], BOOL),
            ("GetForegroundWindow", [], HWND),
            ("GetWindowRect", [HWND, ctypes.POINTER(wintypes.RECT)], BOOL),
            ("MonitorFromRect", [ctypes.POINTER(wintypes.RECT), DWORD], wintypes.HMONITOR),
            ("MonitorFromPoint", [wintypes.POINT, DWORD], wintypes.HMONITOR),
            ("WindowFromPoint", [wintypes.POINT], HWND),
            ("GetWindowTextLengthW", [HWND], ctypes.c_int),
            ("GetWindowTextW", [HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            ("GetClassNameW", [HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            ("GetWindowThreadProcessId", [HWND, ctypes.POINTER(DWORD)], DWORD),
            ("ShowWindow", [HWND, ctypes.c_int], BOOL),
            ("BringWindowToTop", [HWND], BOOL),
            ("SetForegroundWindow", [HWND], BOOL),
        ):
            fn = getattr(u, name)
            fn.argtypes, fn.restype = args, res
        u.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, DWORD, ctypes.c_size_t]
        u.keybd_event.restype = None
        self._u = u
        try:
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]
            dwm.DwmGetWindowAttribute.restype = ctypes.c_long
            self._dwm = dwm
        except (OSError, AttributeError):
            self._dwm = None

    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd) and bool(self._u.IsWindow(hwnd))

    def root(self, hwnd: int) -> int:
        return int(self._u.GetAncestor(hwnd, self.GA_ROOT) or 0) or hwnd

    def root_owner(self, hwnd: int) -> int:
        return int(self._u.GetAncestor(hwnd, self.GA_ROOTOWNER) or 0) or hwnd

    def is_minimized(self, hwnd: int) -> bool:
        return bool(self._u.IsIconic(hwnd))

    def is_visible(self, hwnd: int) -> bool:
        return bool(self._u.IsWindowVisible(hwnd))

    def _dwm_attr(self, hwnd: int, attr: int, out: Any) -> bool:
        if self._dwm is None:
            return False
        ct = self._ctypes
        return self._dwm.DwmGetWindowAttribute(hwnd, attr, ct.byref(out), ct.sizeof(out)) == 0

    def cloaked(self, hwnd: int) -> int:
        value = self._wt.DWORD(0)
        return int(value.value) if self._dwm_attr(hwnd, self.DWMWA_CLOAKED, value) else 0

    def foreground(self) -> int:
        return int(self._u.GetForegroundWindow() or 0)

    def rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        # Extended frame bounds exclude the invisible resize border and are in
        # physical pixels, like UIA BoundingRectangle.
        r = self._wt.RECT()
        if not self._dwm_attr(hwnd, self.DWMWA_EXTENDED_FRAME_BOUNDS, r):
            if not self._u.GetWindowRect(hwnd, self._ctypes.byref(r)):
                return None
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))

    def rect_on_screen(self, rect: tuple[int, int, int, int]) -> bool:
        r = self._wt.RECT(*rect)
        return bool(self._u.MonitorFromRect(self._ctypes.byref(r), self.MONITOR_DEFAULTTONULL))

    def point_on_screen(self, x: int, y: int) -> bool:
        return bool(self._u.MonitorFromPoint(self._wt.POINT(int(x), int(y)), self.MONITOR_DEFAULTTONULL))

    def window_at(self, x: int, y: int) -> int:
        return int(self._u.WindowFromPoint(self._wt.POINT(int(x), int(y))) or 0)

    def title(self, hwnd: int) -> str:
        n = self._u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = self._ctypes.create_unicode_buffer(n + 1)
        self._u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def class_name(self, hwnd: int) -> str:
        buf = self._ctypes.create_unicode_buffer(256)
        self._u.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def process(self, hwnd: int) -> str:
        from wincua_driver.backend.windows import _process_name_for_pid

        pid = self._wt.DWORD(0)
        self._u.GetWindowThreadProcessId(hwnd, self._ctypes.byref(pid))
        return _process_name_for_pid(int(pid.value)) if pid.value else ""

    def restore_and_focus(self, hwnd: int) -> bool:
        """Restore if minimized (a maximized browser stays maximized), raise, and activate."""
        u = self._u
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, self.SW_RESTORE)
        elif not u.IsWindowVisible(hwnd):
            u.ShowWindow(hwnd, self.SW_SHOW)
        u.BringWindowToTop(hwnd)
        if not u.SetForegroundWindow(hwnd) or self.root(self.foreground()) != hwnd:
            # The foreground lock lets a process that just sent input take focus.
            u.keybd_event(self.VK_MENU, 0, 0, 0)
            u.keybd_event(self.VK_MENU, 0, self.KEYEVENTF_KEYUP, 0)
            u.SetForegroundWindow(hwnd)
        return self.root(self.foreground()) == hwnd
