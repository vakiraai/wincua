"""Resolve the 3D Slicer executable on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

_ENV_KEYS = ("SLICER_EXECUTABLE", "WINCUA_SLICER_EXECUTABLE")

# Common stable install folder names (version suffix varies by release).
_PROGRAM_FILES_GLOBS = (
    "Slicer */Slicer.exe",
    "Slicer*/Slicer.exe",
)

ResolveFn = Callable[[], Path | None]


def _env_path() -> Path | None:
    for key in _ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser()
        if p.is_file():
            return p.resolve()
    return None


def _program_files_scan() -> Path | None:
    if sys.platform != "win32":
        return None
    roots: list[Path] = []
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        val = os.environ.get(name)
        if val:
            roots.append(Path(val))
    roots.append(Path(r"C:\Program Files"))
    roots.append(Path(r"C:\Program Files (x86)"))

    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in _PROGRAM_FILES_GLOBS:
            try:
                for match in root.glob(pattern):
                    key = str(match).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    if match.is_file():
                        candidates.append(match.resolve())
            except OSError:
                continue
    if not candidates:
        return None
    # Prefer newest-looking path (longer version segment last alphabetically is ok).
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def resolve_slicer_executable(
    *,
    resolver: ResolveFn | None = None,
    _env: Callable[[], Path | None] | None = None,
    _scan: Callable[[], Path | None] | None = None,
) -> Path:
    """Return path to Slicer.exe or raise SlicerNotInstalledError.

    Order: explicit ``resolver`` → env vars → Program Files scan.
    """
    from wincua_slicer.errors import SlicerNotInstalledError

    if resolver is not None:
        found = resolver()
        if found is not None and found.is_file():
            return found.resolve()

    env_fn = _env if _env is not None else _env_path
    from_env = env_fn()
    if from_env is not None:
        return from_env

    scan_fn = _scan if _scan is not None else _program_files_scan
    from_scan = scan_fn()
    if from_scan is not None:
        return from_scan

    raise SlicerNotInstalledError()