"""Run 3D Slicer with --python-code (API/CLI path; UIA fallback is separate)."""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from wincua_slicer.errors import SlicerCommandError


@dataclass(frozen=True)
class SlicerRunResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]


def build_python_code_snippet(body: str) -> str:
    """Wrap user body with minimal Slicer imports and stdout markers."""
    body = textwrap.dedent(body).strip()
    return textwrap.dedent(
        f"""
        import sys
        try:
            import slicer
        except ImportError as exc:
            print("WINCUA_SLICER_ERROR:import:", exc, file=sys.stderr)
            sys.exit(2)
        try:
        {textwrap.indent(body, "    ")}
        except Exception as exc:
            print("WINCUA_SLICER_ERROR:runtime:", exc, file=sys.stderr)
            sys.exit(3)
        """
    ).strip()


def run_python_code(
    slicer_exe: Path,
    code: str,
    *,
    timeout_s: float = 120.0,
    extra_args: Sequence[str] | None = None,
    popen: type = subprocess.Popen,
) -> SlicerRunResult:
    """Execute ``code`` inside Slicer via ``--python-code``.

    Uses ``--no-splash`` and ``--testing`` so the call can exit without hanging
    on a bare GUI event loop when the script finishes.
    """
    snippet = build_python_code_snippet(code)
    cmd: list[str] = [
        str(slicer_exe),
        "--no-splash",
        "--testing",
        "--python-code",
        snippet,
    ]
    if extra_args:
        cmd[1:1] = list(extra_args)

    proc = popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise SlicerCommandError(
            message=f"Slicer timed out after {timeout_s}s",
        ) from None

    exit_code = proc.returncode if proc.returncode is not None else -1
    ok = exit_code == 0 and "WINCUA_SLICER_ERROR" not in err
    result = SlicerRunResult(
        ok=ok,
        exit_code=exit_code,
        stdout=out or "",
        stderr=err or "",
        command=cmd,
    )
    if not ok:
        detail = (err or out).strip() or "unknown failure"
        raise SlicerCommandError(message=detail, exit_code=exit_code)
    return result


def run_python_script_file(
    slicer_exe: Path,
    script_path: Path,
    *,
    timeout_s: float = 120.0,
    extra_args: Sequence[str] | None = None,
    popen: type = subprocess.Popen,
) -> SlicerRunResult:
    """Run an on-disk ``.py`` file with ``--python-script``."""
    cmd: list[str] = [
        str(slicer_exe),
        "--no-splash",
        "--testing",
        "--python-script",
        str(script_path),
    ]
    if extra_args:
        cmd[1:1] = list(extra_args)

    proc = popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise SlicerCommandError(message=f"Slicer script timed out after {timeout_s}s") from None

    exit_code = proc.returncode if proc.returncode is not None else -1
    ok = exit_code == 0
    result = SlicerRunResult(
        ok=ok,
        exit_code=exit_code,
        stdout=out or "",
        stderr=err or "",
        command=cmd,
    )
    if not ok:
        raise SlicerCommandError(
            message=(err or out).strip() or "script failed",
            exit_code=exit_code,
        )
    return result


def write_temp_script(body: str) -> Path:
    """Write a short script under a temp dir (caller deletes)."""
    body = textwrap.dedent(body).strip() + "\n"
    fd, name = tempfile.mkstemp(suffix=".py", prefix="wincua_slicer_")
    path = Path(name)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(body)
    return path