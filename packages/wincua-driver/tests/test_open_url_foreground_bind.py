"""open_url must bind the new Edge page even when VS Code is foreground."""

from __future__ import annotations

from pathlib import Path

import pytest

from wincua_driver.backend.windows import (
    WindowsBackend,
    normalize_title_text,
    page_title_segment,
)
from wincua_driver.types import Semantic

from test_browser_uia_tree import Info, Node, Page, TITLE, make_backend


class _PopenStub:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


VSCODE_TITLE = "README.md - WinCua - Visual Studio Code"


@pytest.mark.parametrize("edge_title", [f"{TITLE} - Profile 1 - Microsoft\u200b Edge", TITLE])
def test_open_url_binds_browser_not_vscode_foreground(edge_title: str, monkeypatch):
    page = Page()
    page.frame.element_info.name = edge_title
    b = make_backend(page)
    vscode = Node(Info(name=VSCODE_TITLE, control_type="Window", class_name="VisualStudioWindow"))

    def find_once(target: str):
        if target in ("", "foreground", "desktop"):
            return vscode
        if "wincua" in target.lower() or "form" in target.lower() or target.lower() in edge_title.lower():
            return page.frame
        if "edge" in target.lower() and "vscode" not in target.lower():
            return page.frame
        return None

    b._find_window_once = find_once  # type: ignore[method-assign]
    b._find_window = lambda target, timeout_s=0: find_once(target)  # type: ignore[method-assign]
    b._popen = lambda argv: _PopenStub()  # type: ignore[method-assign]
    b._check_process_allowed = lambda name: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        "wincua_driver.backend.windows.resolve_browser_executable",
        lambda name: Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    )

    res = b._open_url(url="https://example.com/wincua-form-fixture.html", browser="msedge", title=TITLE, timeout_s=2.0)
    assert res.ok, res.error
    assert b._window is page.frame
    assert VSCODE_TITLE not in (b._target_title or "")
    assert page_title_segment(b._target_title or "") == TITLE
    assert res.info["pid"] == 4242

    type_res = b._semantic(
        Semantic(name="uia.type", args={"element_id": "auto:fullName", "text": "Ada Lovelace"})
    )
    assert type_res.ok, type_res.error
    assert page.full_name.value == "Ada Lovelace"


def test_normalize_title_strips_edge_zwsp():
    raw = "wincua form demo - Profile 1 - Microsoft\u200b Edge"
    assert normalize_title_text(raw).endswith("Microsoft Edge")
    assert page_title_segment(raw) == "wincua form demo"
    assert WindowsBackend  # module import sanity