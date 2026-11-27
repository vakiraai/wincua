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

__all__ = [
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
    "is_windows",
    "observe",
    "probe_pywinauto",
    "resolve_browser_executable",
    "set_backend",
]

__version__ = "0.1.0"
