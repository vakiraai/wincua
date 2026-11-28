from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wincua_slicer.cli import build_python_code_snippet, run_python_code
from wincua_slicer.errors import SlicerCommandError


def test_build_python_code_snippet_wraps_body():
    s = build_python_code_snippet("print('hi')")
    assert "import slicer" in s
    assert "print('hi')" in s


def test_run_python_code_success(tmp_path: Path):
    exe = tmp_path / "Slicer.exe"
    exe.write_text("", encoding="utf-8")

    proc = MagicMock()
    proc.communicate.return_value = ("WINCUA_SLICER_MARKER:volume:sample\n", "")
    proc.returncode = 0

    def fake_popen(cmd, **kwargs):
        return proc

    result = run_python_code(exe, "print('x')", popen=fake_popen, timeout_s=5)
    assert result.ok
    assert "Slicer.exe" in result.command[0]
    assert "--python-code" in result.command


def test_run_python_code_failure_exit_code(tmp_path: Path):
    exe = tmp_path / "Slicer.exe"
    exe.write_text("", encoding="utf-8")

    proc = MagicMock()
    proc.communicate.return_value = ("", "boom")
    proc.returncode = 1

    with pytest.raises(SlicerCommandError):
        run_python_code(exe, "raise RuntimeError", popen=lambda *a, **k: proc)