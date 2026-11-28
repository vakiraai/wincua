import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wincua_slicer.api import open_volume, probe_installed, select_module
from wincua_slicer.cli import SlicerRunResult
from wincua_slicer.errors import SlicerNotSupportedError, SlicerError


def test_probe_installed_no_slicer():
    with patch("wincua_slicer.api.resolve_slicer_executable", side_effect=Exception("missing")):
        info = probe_installed()
    assert info["installed"] == "no"
    assert "missing" in info["error"]


def test_open_volume_requires_windows():
    if sys.platform == "win32":
        pytest.skip("linux-only guard test")
    with pytest.raises(SlicerNotSupportedError):
        open_volume("/tmp/x.nrrd")


def test_open_volume_missing_file_win32_skip():
    if sys.platform != "win32":
        pytest.skip()
    with patch("wincua_slicer.api.resolve_slicer_executable", return_value=Path("C:/Slicer.exe")):
        with pytest.raises(SlicerError) as exc:
            open_volume("C:/nope/missing.nrrd")
    assert exc.value.code == "volume_not_found"


def test_select_module_calls_run_python_code(tmp_path: Path):
    if sys.platform != "win32":
        pytest.skip()
    exe = tmp_path / "Slicer.exe"
    exe.write_text("", encoding="utf-8")
    fake = SlicerRunResult(ok=True, exit_code=0, stdout="ok", stderr="", command=[])

    with patch("wincua_slicer.api.resolve_slicer_executable", return_value=exe):
        with patch("wincua_slicer.api.run_python_code", return_value=fake) as run:
            got = select_module("Volumes", executable=exe)
    assert got.ok
    run.assert_called_once()
    assert "Volumes" in run.call_args[0][1]