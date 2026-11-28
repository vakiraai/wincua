"""A model "launching" the browser for a page becomes open_url (live Win11 failure 28 Nov 2026)."""

from __future__ import annotations

import pytest

from wincua_providers.prompting import Turn, ToolCallRecord, progress_text, user_turn_text
from wincua_providers.tools import parse_json_tool_calls, to_plan_step

URL = "file:///C:/src/wincua/examples/fixtures/form.html"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"app": "msedge", "url": URL}, {"browser": "msedge", "url": URL}),
        ({"app": "msedge.exe", "args": [URL], "title": "wincua form demo"},
         {"browser": "msedge", "url": URL, "target": "wincua form demo"}),
        ({"app": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "link": URL},
         {"browser": "msedge", "url": URL}),
        ({"app": "Microsoft Edge", "page": URL}, {"browser": "msedge", "url": URL}),
        ({"app": "chrome", "url": "https://example.com"}, {"browser": "chrome", "url": "https://example.com"}),
        ({"app": "msedge"}, {"browser": "msedge"}),  # no page: open_url will say url is missing
    ],
)
def test_launch_of_a_browser_is_open_url(args, expected):
    step = to_plan_step("launch", args)
    assert (step.kind, step.name, step.args) == ("tool", "open_url", expected)


def test_launch_of_other_apps_is_unchanged():
    step = to_plan_step("launch", {"app": "calculator", "title": "Calculator"})
    assert step.name == "launch" and step.args == {"app": "calculator", "title": "Calculator"}


def test_json_reply_with_start_msedge_alias_is_open_url():
    reply = f'{{"actions": [{{"tool": "start", "args": {{"app": "msedge", "url": "{URL}"}}}}]}}'
    steps = [to_plan_step(n, a) for n, a in parse_json_tool_calls(reply)]
    assert [(s.name, s.args.get("url")) for s in steps] == [("open_url", URL)]


def test_progress_lists_succeeded_calls_in_order():
    turns = [
        Turn(0, calls=[ToolCallRecord("c0", "launch", {"app": "calculator", "title": "Calculator"}, "ok")]),
        Turn(1, calls=[
            ToolCallRecord("c1", "invoke", {"element_id": "auto:clearButton", "auto_id": "clearButton"}, "ok"),
            ToolCallRecord("c2", "invoke", {"element_id": "auto:percentButton"}, "FAILED: no element"),
        ]),
    ]
    text = progress_text(turns)
    assert text.startswith('Already succeeded, in order: launch(app="calculator", title="Calculator"); '
                           'invoke(element_id="auto:clearButton").')
    assert "percentButton" not in text and "Do not start over" in text
    prompt = user_turn_text("compute", None, first=False, progress=text)
    assert prompt.index("Already succeeded") < prompt.index("Continue the task: compute")
    assert progress_text([]) == "" and "Already" not in user_turn_text("t", None, first=False)
