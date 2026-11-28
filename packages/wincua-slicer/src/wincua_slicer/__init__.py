"""wincua-slicer — 3D Slicer / org Slicer helpers (Phase 2 pack).

**Strategy:** Slicer Python API via ``--python-code`` / CLI first; generic UIA
click/type is a documented fallback in the driver (weak Qt trees). Use
``wincua_driver.observe`` + vision only when API helpers cannot reach the UI.
"""

from wincua_slicer.api import LaunchResult, launch, open_file, open_volume, probe_installed, select_module
from wincua_slicer.cli import SlicerRunResult, run_python_code
from wincua_slicer.discovery import resolve_slicer_executable
from wincua_slicer.errors import (
    SlicerCommandError,
    SlicerError,
    SlicerNotInstalledError,
    SlicerNotSupportedError,
)

__version__ = "0.1.0"

__all__ = [
    "LaunchResult",
    "SlicerCommandError",
    "SlicerError",
    "SlicerNotInstalledError",
    "SlicerNotSupportedError",
    "SlicerRunResult",
    "launch",
    "open_file",
    "open_volume",
    "probe_installed",
    "resolve_slicer_executable",
    "run_python_code",
    "select_module",
]