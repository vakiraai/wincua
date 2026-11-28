"""High-level 3D Slicer helpers — API/CLI first; UIA fallback lives in wincua-driver later."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from wincua_slicer.cli import SlicerRunResult, run_python_code
from wincua_slicer.discovery import resolve_slicer_executable
from wincua_slicer.errors import SlicerNotSupportedError


@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    executable: Path
    pid: int | None = None
    message: str = ""


def require_windows_for_live() -> None:
    if sys.platform != "win32":
        raise SlicerNotSupportedError()


def launch(
    *,
    executable: Path | None = None,
    extra_args: Sequence[str] | None = None,
    wait: bool = False,
    timeout_s: float = 60.0,
    popen: type = subprocess.Popen,
) -> LaunchResult:
    """Start Slicer GUI (foreground). Prefer semantic helpers for volume load."""
    require_windows_for_live()
    exe = executable or resolve_slicer_executable()
    cmd: list[str] = [str(exe), "--no-splash"]
    if extra_args:
        cmd.extend(extra_args)
    if wait:
        proc = popen(cmd)
        proc.wait(timeout=timeout_s)
        return LaunchResult(
            ok=proc.returncode == 0,
            executable=exe,
            pid=proc.pid,
            message="exited" if proc.returncode is not None else "launched",
        )
    proc = popen(cmd)
    return LaunchResult(ok=True, executable=exe, pid=proc.pid, message="launched")


def select_module(
    module_name: str,
    *,
    executable: Path | None = None,
    timeout_s: float = 90.0,
) -> SlicerRunResult:
    """Select a Slicer module by name (e.g. ``Volumes``, ``DICOM``)."""
    require_windows_for_live()
    exe = executable or resolve_slicer_executable()
    safe_name = module_name.replace("'", "\\'")
    code = f"""
    slicer.util.selectModule('{safe_name}')
    print('WINCUA_SLICER_MARKER:module:{module_name}')
    """
    return run_python_code(exe, code, timeout_s=timeout_s)


def open_volume(
    path: str | Path,
    *,
    module: str = "Volumes",
    executable: Path | None = None,
    timeout_s: float = 180.0,
) -> SlicerRunResult:
    """Load a scalar volume via ``slicer.util.loadVolume``."""
    require_windows_for_live()
    exe = executable or resolve_slicer_executable()
    vol = Path(path)
    if not vol.is_file():
        from wincua_slicer.errors import SlicerError

        raise SlicerError(
            code="volume_not_found",
            message=f"Volume file does not exist: {vol}",
            hint="Pass an absolute path to .nrrd, .nii, .nii.gz, etc.",
        )
    # Escape for Python string literal
    path_lit = str(vol.resolve()).replace("\\", "\\\\").replace("'", "\\'")
    mod_lit = module.replace("'", "\\'")
    code = f"""
    slicer.util.selectModule('{mod_lit}')
    node = slicer.util.loadVolume(r'{path_lit}')
    name = node.GetName() if node else ''
    print('WINCUA_SLICER_MARKER:volume:' + str(name))
    """
    return run_python_code(exe, code, timeout_s=timeout_s)


def open_file(
    path: str | Path,
    *,
    properties: dict[str, str] | None = None,
    executable: Path | None = None,
    timeout_s: float = 180.0,
) -> SlicerRunResult:
    """Load arbitrary data via ``slicer.util.loadFile`` when available."""
    require_windows_for_live()
    exe = executable or resolve_slicer_executable()
    vol = Path(path)
    if not vol.is_file():
        from wincua_slicer.errors import SlicerError

        raise SlicerError(
            code="file_not_found",
            message=f"File does not exist: {vol}",
        )
    path_lit = str(vol.resolve()).replace("\\", "\\\\").replace("'", "\\'")
    props = properties or {}
    props_lines = "".join(
        f"    properties['{k.replace(chr(39), '')}'] = '{v.replace(chr(39), '')}'\n"
        for k, v in props.items()
    )
    code = f"""
    properties = {{}}
{props_lines}
    try:
        node = slicer.util.loadFile(r'{path_lit}', properties)
    except AttributeError:
        node = slicer.util.loadVolume(r'{path_lit}')
    name = node.GetName() if node else ''
    print('WINCUA_SLICER_MARKER:file:' + str(name))
    """
    return run_python_code(exe, code, timeout_s=timeout_s)


def probe_installed(*, executable: Path | None = None) -> dict[str, str]:
    """Return discovery info without launching GUI (safe to call from demos)."""
    try:
        exe = executable or resolve_slicer_executable()
        return {
            "installed": "yes",
            "executable": str(exe),
            "platform": sys.platform,
        }
    except Exception as exc:
        return {
            "installed": "no",
            "error": str(exc),
            "platform": sys.platform,
        }