import pytest

from wincua_driver.types import Click, Invoke, Key, Semantic, Type
from wincua_mcp.policy import AllowList, classify_risk, exe_key, resolve_window


def test_exe_key_normalises_paths_and_browser_aliases():
    assert exe_key(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe") == "msedge.exe"
    assert exe_key("MSEDGE.EXE") == "msedge.exe"
    assert exe_key("edge") == "msedge.exe"
    assert exe_key("calc") == "calc"


def test_empty_allow_list_allows_nothing():
    allow = AllowList([])
    assert not allow
    assert not allow.allows_app("calc")
    assert not allow.allows_window({"title": "Calculator", "process": "ApplicationFrameHost.exe"})


def test_calculator_aliases():
    allow = AllowList(["calc.exe"])
    assert allow.allows_app("calculator")
    assert allow.allows_app("CalculatorApp.exe")
    assert allow.allows_window({"title": "Calculator", "process": "ApplicationFrameHost.exe"})
    # Other UWP apps share ApplicationFrameHost.exe and must stay denied.
    assert not allow.allows_window({"title": "Settings", "process": "ApplicationFrameHost.exe"})
    assert not allow.allows_app("notepad.exe")


def test_process_match_is_case_insensitive():
    allow = AllowList(["MSEdge.exe"])
    assert allow.allows_window({"title": "x - Microsoft Edge", "process": "msedge.exe"})
    assert allow.allows_app("edge")


WINDOWS = [
    {"title": "Untitled - Notepad", "process": "notepad.exe"},
    {"title": "Calculator", "process": "ApplicationFrameHost.exe", "foreground": True},
    {"title": "form - Microsoft Edge", "process": "msedge.exe"},
]


def test_resolve_window():
    assert resolve_window(WINDOWS, "foreground")["title"] == "Calculator"
    assert resolve_window(WINDOWS, "calc")["title"] == "Calculator"
    assert resolve_window(WINDOWS, "edge")["process"] == "msedge.exe"
    assert resolve_window(WINDOWS, "nothing") is None
    assert resolve_window([], "foreground") is None


@pytest.mark.parametrize(
    "action,name",
    [
        (Type(text="hi", submit=True), None),
        (Click(x=10, y=20), None),
        (Invoke(element_id="name:Submit form"), None),
        (Click(element_id="e9"), "Delete file"),
        (Key(keys="alt+F4"), None),
        (Key(keys="ctrl+w"), None),
        (Key(keys="win+r"), None),
        (Key(keys="Delete"), None),
        (Semantic("excel.set_range"), None),
    ],
)
def test_risky(action, name):
    assert classify_risk(action, element_name=name)


@pytest.mark.parametrize(
    "action,name",
    [
        (Type(text="hi"), None),
        (Invoke(element_id="auto:num7Button"), "Seven"),
        (Click(element_id="e2"), "One"),
        (Key(keys="enter"), None),
        (Key(keys="ctrl+a"), None),
        (Key(keys="escape"), None),
        (Semantic("focus"), None),
    ],
)
def test_not_risky(action, name):
    assert classify_risk(action, element_name=name) is None
