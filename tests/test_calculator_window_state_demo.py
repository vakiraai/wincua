"""examples/calculator_arith_demo.py window-state flags (--click, --pause, --simulate) on the mock backend."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _demo():
    sys.path.insert(0, str(EXAMPLES))
    spec = importlib.util.spec_from_file_location("calculator_arith_demo", EXAMPLES / "calculator_arith_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    # Registered so teardown undoes the demo's own os.environ write for --no-auto-focus.
    monkeypatch.setenv("WINCUA_AUTO_FOCUS", "")


def test_click_and_pause_steps():
    steps = _demo().build_calculator_steps(pause_s=8, click=True)
    assert [s.args for s in steps if s.name == "wait"][2] == {"seconds": 8}
    clicks = [s for s in steps if s.name == "click"]
    assert clicks[0].args == {"element_id": "auto:clearButton"} and len(clicks) == 7
    assert not any(s.name == "invoke" for s in steps)


def test_mock_click_run_passes(capsys):
    assert asyncio.run(_demo().main(["--mock", "--click"])) == 0
    assert "message='17 + 25 == 42'" in capsys.readouterr().out


def test_simulated_cover_fails_clicks_but_not_invokes(capsys, monkeypatch):
    demo = _demo()
    assert asyncio.run(demo.main(["--mock", "--click", "--simulate", "target_obscured", "--no-auto-focus"])) == 2
    out = capsys.readouterr().out
    assert "[target_obscured]" in out and "Hint:" in out
    monkeypatch.setenv("WINCUA_AUTO_FOCUS", "")
    assert asyncio.run(demo.main(["--mock", "--simulate", "target_obscured"])) == 0
