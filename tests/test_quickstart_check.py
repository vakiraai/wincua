"""scripts/quickstart_check.py on a non-Windows host."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _check():
    spec = importlib.util.spec_from_file_location("quickstart_check", ROOT / "scripts" / "quickstart_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)


def test_no_config_only_warns(capsys):
    assert _check().main([]) == 0
    out = capsys.readouterr().out
    assert "[ok]   import wincua_mcp" in out and "[ok]   import mcp" in out
    assert "[warn] config: no wincua.toml found" in out
    assert "[FAIL]" not in out


def test_example_config_and_unreachable_ollama(tmp_path, capsys):
    text = (ROOT / "wincua.example.toml").read_text(encoding="utf-8")
    (tmp_path / "wincua.toml").write_text(text.replace("127.0.0.1:11434", "127.0.0.1:9", 1), encoding="utf-8")
    assert _check().main([]) == 0
    out = capsys.readouterr().out
    assert f"[ok]   config: {tmp_path / 'wincua.toml'}" in out
    assert "[warn] provider ollama: ollama: unreachable" in out
    assert "ollama pull qwen2.5vl:7b" in out
    assert '"--config"' in out and "wincua-mcp" in out


def test_missing_package_fails(monkeypatch, capsys):
    mod = _check()
    monkeypatch.setattr(mod, "MODULES", mod.MODULES + ("wincua_definitely_missing",))
    assert mod.main([]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] import wincua_definitely_missing" in out
    assert out.count("fix: python -m pip install -e") == 1


def test_bad_config_fails(tmp_path, capsys):
    (tmp_path / "wincua.toml").write_text("[provider\n", encoding="utf-8")
    assert _check().main([]) == 1
    assert "[FAIL] config:" in capsys.readouterr().out


def test_quickstart_install_line_matches_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert _check().INSTALL in readme
