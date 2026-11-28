"""Linux-safe tests for slicer_open_volume_demo --mock."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEMO = _ROOT / "examples" / "slicer_open_volume_demo.py"


def test_slicer_demo_mock_passes():
    env = {**dict(__import__("os").environ)}
    for p in (_ROOT / "packages" / "wincua-slicer" / "src",):
        env["PYTHONPATH"] = str(p) + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, str(_DEMO), "--mock", "--volume", "/tmp/sample.nrrd"],
        capture_output=True,
        text=True,
        cwd=_ROOT,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "golden 'volume loaded': PASS" in proc.stdout