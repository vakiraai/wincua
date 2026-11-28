"""examples/browser_form_demo.py end to end on the mock backend."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
GOLDEN_PASS = "golden 'OK: Ada Lovelace <ada@example.com>': PASS"


def _demo():
    sys.path.insert(0, str(EXAMPLES))
    spec = importlib.util.spec_from_file_location("browser_form_demo", EXAMPLES / "browser_form_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)


def test_fixture_exposes_stable_ids():
    html = (EXAMPLES / "fixtures" / "form.html").read_text(encoding="utf-8")
    for snippet in ('id="fullName"', 'for="fullName"', 'id="email"', 'for="email"', 'id="submitButton"', ">Submit form<"):
        assert snippet in html


def test_scripted_mock_run_passes(capsys):
    assert asyncio.run(_demo().main(["--mock"])) == 0
    out = capsys.readouterr().out
    assert GOLDEN_PASS in out
    assert "type(element_id='auto:fullName'" in out


def test_scripted_with_cli_hitl_approves_submit(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("approve\n"))
    assert asyncio.run(_demo().main(["--mock", "--hitl", "cli"])) == 0
    assert GOLDEN_PASS in capsys.readouterr().out


def test_scripted_with_cli_hitl_reject_fails_golden(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("reject\n"))
    code = asyncio.run(_demo().main(["--mock", "--hitl", "cli"]))
    out = capsys.readouterr().out
    assert code != 0
    assert "golden 'OK: Ada Lovelace <ada@example.com>': FAIL" in out


def test_human_plans_every_step(monkeypatch, capsys):
    commands = "type @auto:fullName Ada Lovelace\ntype @auto:email ada@example.com\ninvoke auto:submitButton\ndone\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(commands))
    assert asyncio.run(_demo().main(["--mock", "--provider", "null", "--hitl", "cli"])) == 0
    assert GOLDEN_PASS in capsys.readouterr().out


def test_dump_tree_flag_prints_tree(capsys):
    assert asyncio.run(_demo().main(["--mock", "--dump-tree"])) == 0
    out = capsys.readouterr().out
    assert "mock UIA dump" in out and "[Edit] id='fullName_edit' name='Full name'" in out
    assert "UIA tree dump: recordings" in out
