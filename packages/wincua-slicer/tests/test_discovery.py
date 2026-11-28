from pathlib import Path

import pytest

from wincua_slicer.discovery import resolve_slicer_executable
from wincua_slicer.errors import SlicerNotInstalledError


def test_resolve_uses_env_resolver_first(tmp_path: Path):
    fake = tmp_path / "Slicer.exe"
    fake.write_text("", encoding="utf-8")

    got = resolve_slicer_executable(resolver=lambda: fake)
    assert got == fake.resolve()


def test_resolve_raises_when_missing():
    with pytest.raises(SlicerNotInstalledError) as exc:
        resolve_slicer_executable(resolver=lambda: None, _env=lambda: None, _scan=lambda: None)
    assert exc.value.code == "slicer_not_installed"
    assert "download.slicer.org" in exc.value.message