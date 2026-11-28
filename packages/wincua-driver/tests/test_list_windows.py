import asyncio

from wincua_driver.backend.mock import MockBackend
from wincua_driver.backend.null import NullBackend
from wincua_driver.backend.windows import WindowsBackend


class _Info:
    def __init__(self, name, class_name):
        self.name = name
        self.class_name = class_name
        self.control_type = "Window"
        self.automation_id = ""
        self.process_id = 0


class _Win:
    def __init__(self, title, handle, visible=True, class_name="Notepad"):
        self.element_info = _Info(title, class_name)
        self.handle = handle
        self._title = title
        self._visible = visible

    def window_text(self):
        return self._title

    def is_visible(self):
        return self._visible


class _Desktop:
    def windows(self):
        return [
            _Win("Calculator", 11, class_name="ApplicationFrameWindow"),
            _Win("", 12),
            _Win("Hidden", 13, visible=False),
            _Win("Untitled - Notepad", 14),
        ]


def test_windows_backend_list_windows_filters_untitled_and_hidden():
    backend = object.__new__(WindowsBackend)
    backend._desktop = lambda: _Desktop()  # type: ignore[method-assign]
    rows = asyncio.run(backend.list_windows())
    assert [r["title"] for r in rows] == ["Calculator", "Untitled - Notepad"]
    assert rows[0]["class_name"] == "ApplicationFrameWindow"
    assert rows[0]["handle"] == 11
    assert set(rows[0]) == {"title", "process", "pid", "handle", "class_name", "foreground"}


def test_mock_and_null_list_windows():
    rows = asyncio.run(MockBackend().list_windows())
    assert {r["process"] for r in rows} >= {"ApplicationFrameHost.exe", "msedge.exe"}
    assert asyncio.run(NullBackend().list_windows()) == []
