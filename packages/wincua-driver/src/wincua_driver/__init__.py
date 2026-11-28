"""wincua-driver — observe / act substrate."""

from wincua_driver.act import act
from wincua_driver.backend import get_backend, set_backend
from wincua_driver.backend.windows import (
    ensure_pywinauto,
    is_windows,
    probe_pywinauto,
    resolve_browser_executable,
)
from wincua_driver.observe import observe
from wincua_driver.types import (
    ActionResult,
    Click,
    ElementRef,
    ImageRef,
    Invoke,
    Key,
    Observation,
    Semantic,
    StructuredError,
    Type,
)
from wincua_driver.window_state import WINDOW_STATE_CODES, is_window_state_error

__all__ = [
    "WINDOW_STATE_CODES",
    "ActionResult",
    "Click",
    "ElementRef",
    "ImageRef",
    "Invoke",
    "Key",
    "Observation",
    "Semantic",
    "StructuredError",
    "Type",
    "act",
    "ensure_pywinauto",
    "get_backend",
    "is_window_state_error",
    "is_windows",
    "observe",
    "probe_pywinauto",
    "resolve_browser_executable",
    "set_backend",
]

__version__ = "0.1.0"
