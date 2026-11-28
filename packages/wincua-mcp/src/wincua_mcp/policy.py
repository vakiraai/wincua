"""Process allow-list and risky-action classification for MCP calls."""

from __future__ import annotations

import re
from pathlib import PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence

from wincua_driver.types import Action, Click, Invoke, Key, Semantic, Type

CALCULATOR_ALIASES = frozenset(
    {
        "calc",
        "calc.exe",
        "calculator",
        "calculator:",
        "calculatorapp",
        "calculatorapp.exe",
        "microsoft.windowscalculator_8wekyb3d8bbwe!app",
    }
)
_BROWSER_EXES = {
    "edge": "msedge.exe",
    "msedge": "msedge.exe",
    "chrome": "chrome.exe",
}
UWP_FRAME_HOST = "applicationframehost.exe"

# Ctrl/Alt chords that only edit or navigate; any other modifier chord is risky.
_SAFE_CHORDS = frozenset(
    {"ctrl+a", "ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+f", "ctrl+l", "ctrl+home", "ctrl+end"}
)
_RISKY_KEYS = frozenset({"delete", "del", "shift+delete", "shift+del", "alt+f4"})
_RISKY_NAME = re.compile(
    r"\b(submit|send|delete|remove|pay|purchase|buy|order|install|uninstall|sign ?out|log ?out|"
    r"shut ?down|restart|discard|erase|format|close|exit|quit|publish|transfer)\b",
    re.IGNORECASE,
)
SAFE_SEMANTICS = frozenset({"wait", "system.wait", "focus", "app.focus"})


def exe_key(name: str) -> str:
    """Normalise an allow-list entry / process name / app path to a lowercase basename."""
    raw = (name or "").strip().strip('"')
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in CALCULATOR_ALIASES:
        return lowered
    base = PureWindowsPath(raw).name.lower()
    return _BROWSER_EXES.get(base, base)


class AllowList:
    """Fail-closed process allow-list. An empty list allows nothing."""

    def __init__(self, entries: Iterable[str]) -> None:
        self.entries = [e for e in entries if e.strip()]
        self._keys = {exe_key(e) for e in self.entries}

    def __bool__(self) -> bool:
        return bool(self._keys)

    @property
    def allows_calculator(self) -> bool:
        return bool(self._keys & CALCULATOR_ALIASES)

    def allows_app(self, app: str) -> bool:
        key = exe_key(app)
        if not key:
            return False
        if key in CALCULATOR_ALIASES:
            return self.allows_calculator or UWP_FRAME_HOST in self._keys
        return key in self._keys

    def allows_window(self, window: Mapping[str, Any]) -> bool:
        process = exe_key(str(window.get("process") or ""))
        if process in self._keys:
            return True
        title = str(window.get("title") or "").strip()
        # Store Calculator's top-level window belongs to ApplicationFrameHost.exe.
        return process in {UWP_FRAME_HOST, "calculatorapp.exe"} and title == "Calculator" and self.allows_calculator


def is_calculator_target(target: str) -> bool:
    return (target or "").strip().lower() in CALCULATOR_ALIASES


def resolve_window(windows: Sequence[Mapping[str, Any]], target: str) -> Mapping[str, Any] | None:
    """Pick the window the driver would act on for ``target`` (title substring, like the driver)."""
    t = (target or "").strip()
    if t.lower() in ("", "foreground", "desktop"):
        for w in windows:
            if w.get("foreground"):
                return w
        return windows[0] if windows else None
    if is_calculator_target(t):
        for w in windows:
            if str(w.get("title") or "").strip() == "Calculator":
                return w
        return None
    for w in windows:
        if str(w.get("title") or "").strip() == t:
            return w
    for w in windows:
        if t.lower() in str(w.get("title") or "").lower():
            return w
    return None


def _normalise_keys(keys: str) -> str:
    k = re.sub(r"\s+", "", (keys or "").lower())
    k = k.replace("control", "ctrl").replace("{", "").replace("}", "")
    return k


def classify_risk(action: Action, *, element_name: str | None = None) -> str | None:
    """Return a human-readable reason if ``action`` is risky, else ``None``."""
    if isinstance(action, Type):
        if action.submit:
            return "types text and submits (Enter)"
        return None
    if isinstance(action, Click) and action.element_id is None:
        return f"clicks raw screen coordinates ({action.x}, {action.y})"
    if isinstance(action, (Click, Invoke)):
        name = element_name or ""
        eid = action.element_id or ""
        if eid.startswith("name:") and not name:
            name = eid[5:]
        if _RISKY_NAME.search(name):
            return f"activates control {name!r}"
        return None
    if isinstance(action, Key):
        k = _normalise_keys(action.keys)
        if k in _RISKY_KEYS:
            return f"presses {action.keys!r}"
        parts = set(k.split("+"))
        if parts & {"win", "lwin", "rwin", "windows", "super"}:
            return f"presses Windows-key chord {action.keys!r}"
        if parts & {"ctrl", "alt"} and k not in _SAFE_CHORDS:
            return f"presses chord {action.keys!r}"
        return None
    if isinstance(action, Semantic):
        if action.name in SAFE_SEMANTICS:
            return None
        return f"runs semantic action {action.name!r}"
    return "unknown action type"
