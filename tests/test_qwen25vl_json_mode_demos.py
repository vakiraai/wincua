"""Both model demos against a fake Ollama serving qwen2.5vl:7b (no native tools, JSON replies).

The fake is a real HTTP server, so the demos' preflight (``/api/tags``,
``/api/generate``), the urllib transport and the JSON-reply path all run as on
Windows. Replies mimic what qwen2.5vl writes: fenced JSON with prose, several
objects in one reply, alias names, nested calls, near-JSON and cut-off output.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
GOLDEN_PASS = "golden 'OK: Ada Lovelace <ada@example.com>': PASS"
URL = "__URL__"  # replaced with the fixture URI the demo put in the task


def _load(name: str):
    sys.path.insert(0, str(EXAMPLES))
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeQwen25VL:
    """Ollama look-alike: qwen2.5vl:7b pulled, tools refused, chat replies from a script."""

    model = "qwen2.5vl:7b"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.chats: list[dict] = []
        self.generate_calls = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, obj: dict) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                self._send(200, {"models": [{"name": fake.model}]})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/generate":
                    fake.generate_calls += 1
                    return self._send(200, {"model": fake.model, "response": "", "done": True})
                fake.chats.append(body)
                if "tools" in body:
                    return self._send(400, {"error": f"registry.ollama.ai/library/{fake.model} does not support tools"})
                if not fake.replies:
                    return self._send(200, _reply('{"actions": [{"tool": "abort", "args": {"reason": "script over"}}]}'))
                text = fake.replies.pop(0)
                if URL in text:
                    task = body["messages"][1]["content"] if len(body["messages"]) > 2 else body["messages"][-1]["content"]
                    text = text.replace(URL, re.search(r"url='([^']+)'", task).group(1))
                return self._send(200, _reply(text))

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _reply(content: str) -> dict:
    return {"model": "qwen2.5vl:7b", "message": {"role": "assistant", "content": content}, "done": True}


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    # A local model server must be reached directly even with a proxy configured.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    def write(base_url: str) -> str:
        example = (Path(__file__).resolve().parents[1] / "wincua.example.toml").read_text(encoding="utf-8")
        text = example.replace('base_url = "http://127.0.0.1:11434"', f'base_url = "{base_url}"', 1)
        path = tmp_path / "wincua.toml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    return write


OPEN_FENCED = (
    "I'll open the form first.\n```json\n"
    '{"actions": [{"tool": "open_url", "args": {"url": "__URL__", "browser": "msedge", "target": "wincua form demo"}}]}\n```'
)
FILL_CLEAN = json.dumps(
    {
        "actions": [
            {"tool": "type", "args": {"element_id": "auto:fullName", "text": "Ada Lovelace"}},
            {"tool": "type", "args": {"element_id": "auto:email", "text": "ada@example.com"}},
            {"tool": "invoke", "args": {"element_id": "auto:submitButton"}},
        ]
    }
)
DONE = '{"actions": [{"tool": "done", "args": {"message": "OK: Ada Lovelace <ada@example.com>"}}]}'

BROWSER_SCRIPTS = {
    "clean": [OPEN_FENCED, FILL_CLEAN, DONE],
    "several_objects_alias_names_row_ids": [
        '{"action": "navigate", "url": "__URL__", "browser": "msedge"}',
        '{"tool": "type_text", "args": {"element": "auto:fullName Edit \\"Full name\\"", "value": "Ada Lovelace"}}\n'
        '{"tool": "type_text", "args": {"element": "auto:email Edit \\"Email\\"", "value": "ada@example.com"}}\n'
        '{"tool": "press_button", "args": {"id": "name:Submit form"}}',
        '{"tool": "finish", "args": {"result": "submitted"}}',
    ],
    "nested_single_quotes_trailing_commas": [
        "{'tool': 'open_url', 'args': {'url': '__URL__', 'browser': 'msedge', 'target': 'wincua form demo'}}",
        '{"tool": "type", "args": {"element_id": "auto:fullName", "text": "Ada Lovelace", '
        '"tool": "type", "args": {"element_id": "auto:email", "text": "ada@example.com", '
        '"args": {"tool": "invoke", "element_id": "auto:submitButton"}}},}',
        DONE,
    ],
    "prose_then_cut_off_reply": [
        "Sure, here is what I would do: open the page.",  # no JSON: the agent nudges
        OPEN_FENCED,
        FILL_CLEAN[:-2] + ', {"tool": "wait", "args": {"seconds": 1}}, {"tool": "do',  # cut off mid-call
        DONE,
    ],
}


@pytest.mark.parametrize("script", sorted(BROWSER_SCRIPTS))
def test_browser_form_demo_passes_with_qwen25vl_json_replies(script, config, capsys):
    with FakeQwen25VL(BROWSER_SCRIPTS[script]) as fake:
        path = config(fake.base_url)
        code = asyncio.run(_load("browser_form_demo").main(["--mock", "--provider", "ollama", "--config", path]))
    out = capsys.readouterr()
    assert code == 0, out.out + out.err
    assert GOLDEN_PASS in out.out
    assert "replies=JSON (no native tools), screenshots=off" in out.out
    assert "[ok] ollama: ollama ok at" in out.out and "model 'qwen2.5vl:7b' loaded" in out.out
    assert "unavailable" not in out.err
    assert fake.generate_calls == 1
    for body in fake.chats:
        assert "tools" not in body
        assert body["format"]["properties"]["actions"]["items"]["properties"]["tool"]["enum"][0] == "launch"
        assert body["options"]["num_ctx"] == 8192 and body["options"]["num_predict"] == 512
        last = body["messages"][-1]
        assert "images" not in last and last["content"].rstrip().endswith("Do not repeat actions that already succeeded.")
    replay = [m for m in fake.chats[-1]["messages"] if m["role"] == "assistant"]
    assert replay and all(json.loads(m["content"]).keys() == {"actions"} for m in replay if m["content"].startswith("{"))


CALC_SCRIPTS = {
    "batched_actions": [
        '{"actions": [{"tool": "launch", "args": {"app": "calculator", "title": "Calculator"}}]}',
        json.dumps(
            {"actions": [{"tool": "invoke", "args": {"element_id": f"auto:{b}"}}
                         for b in ("clearButton", "num1Button", "num7Button", "plusButton",
                                   "num2Button", "num5Button", "equalButton")]}
        ),
        '{"actions": [{"tool": "done", "args": {"message": "42"}}]}',
    ],
    "nested_and_fenced": [
        '```json\n{"tool": "launch", "args": {"app": "calculator", "title": "Calculator"}}\n```',
        json.dumps(
            {"tool": "invoke", "args": {"element_id": "auto:clearButton", "tool": "invoke", "args": {
                "element_id": "auto:num1Button", "tool": "invoke", "args": {
                    "element_id": "auto:num7Button", "tool": "invoke", "args": {
                        "element_id": "auto:plusButton", "tool": "invoke", "args": {
                            "element_id": "auto:num2Button", "tool": "invoke", "args": {
                                "element_id": "auto:num5Button", "tool": "invoke", "args": {
                                    "element_id": "auto:equalButton"}}}}}}}}
        ),
        '{"tool": "done", "args": {"message": "42"},}',
    ],
}


@pytest.mark.parametrize("script", sorted(CALC_SCRIPTS))
def test_calculator_llm_demo_passes_with_qwen25vl_json_replies(script, config, capsys):
    with FakeQwen25VL(CALC_SCRIPTS[script]) as fake:
        path = config(fake.base_url)
        code = asyncio.run(_load("calculator_llm_demo").main(["--mock", "--provider", "ollama", "--config", path]))
    out = capsys.readouterr()
    assert code == 0, out.out + out.err
    assert "expected 42 -> PASS" in out.out
    assert all("tools" not in body for body in fake.chats)


def test_repeating_the_same_reply_still_escalates(config, capsys, monkeypatch):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO())
    same = '{"actions": [{"tool": "invoke", "args": {"element_id": "auto:clearButton"}}]}'
    with FakeQwen25VL(['{"tool": "launch", "args": {"app": "calculator"}}'] + [same] * 6) as fake:
        path = config(fake.base_url)
        code = asyncio.run(_load("calculator_llm_demo").main(["--mock", "--provider", "ollama", "--config", path]))
    out = capsys.readouterr().out
    assert code != 0
    assert "same actions repeated 3 turns" in out
    assert len(fake.chats) == 4
