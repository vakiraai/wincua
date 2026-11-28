"""Why a provider was skipped is printed at once, in one short line, and shown to the human."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fake_http import FakeTransport, ollama_tool_reply
from wincua_providers import FallbackProvider, NullProvider, OllamaProvider
from wincua_providers._http import is_loopback_url, urllib_transport
from wincua_providers.fallback import short_reason


def run(coro):
    return asyncio.run(coro)


def chain_with(transport: FakeTransport, **kw):
    lines: list[str] = []
    chain = FallbackProvider(
        [OllamaProvider(model="qwen2.5:7b", transport=transport, retries=0, **kw), NullProvider()],
        notify=lines.append,
    )
    return chain, lines


NOT_PULLED = {"error": 'model "qwen2.5:7b" not found, try pulling it first'}


@pytest.mark.parametrize(
    ("transport", "expect"),
    [
        (FakeTransport().add(404, NOT_PULLED), ['not found, try pulling it first', "ollama pull qwen2.5:7b"]),
        (FakeTransport().fail(TimeoutError("timed out")), ["timed out after", "raise timeout_s"]),
        (FakeTransport().fail(ConnectionError("refused")), ["unreachable at http://127.0.0.1:11434"]),
        (FakeTransport().add(500, {"error": "model requires more system memory (5.6 GiB) than is available"}),
         ["HTTP 500: model requires more system memory"]),
    ],
)
def test_fallback_reason_is_notified_once_as_one_short_line(transport, expect):
    chain, lines = chain_with(transport)
    res = run(chain.plan(task="Fill in the form"))
    assert res.provider == "null" and not res.available
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("ollama unavailable: ") and line.endswith("; falling back to null")
    assert "\n" not in line and len(line) < 300
    for frag in expect:
        assert frag in line
    question = res.steps[0].args["question"]
    assert question.startswith("ollama unavailable: ") and "No model available" in question
    assert chain.events == lines


def test_fallback_default_notify_prints_to_stderr(capsys):
    chain = FallbackProvider(
        [OllamaProvider(transport=FakeTransport().add(404, NOT_PULLED), retries=0), NullProvider()]
    )
    run(chain.plan(task="t"))
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("[wincua] ollama unavailable: ")


def test_no_notification_when_the_model_answers():
    t = FakeTransport().add(200, ollama_tool_reply(("done", {"message": "ok"})))
    chain, lines = chain_with(t)
    res = run(chain.plan(task="t"))
    assert res.available and res.provider == "ollama" and lines == []


def test_short_reason_collapses_and_truncates():
    assert short_reason("a\n  b") == "a b"
    assert short_reason(None) == "no reason given"
    long = short_reason("x" * 500)
    assert len(long) == 200 and long.endswith("…")


def test_ollama_warm_up_loads_the_model_or_says_why():
    ok_t = FakeTransport().add(200, {"model": "qwen2.5:7b", "response": "", "done": True})
    ok, detail = run(OllamaProvider(model="qwen2.5:7b", keep_alive="10m", transport=ok_t).warm_up())
    assert ok and "loaded" in detail
    req = ok_t.requests[0]
    assert req.url.endswith("/api/generate") and req.body == {
        "model": "qwen2.5:7b", "prompt": "", "stream": False, "keep_alive": "10m",
    }
    bad, detail = run(OllamaProvider(model="qwen2.5:7b", transport=FakeTransport().add(404, NOT_PULLED)).warm_up())
    assert not bad and "ollama pull qwen2.5:7b" in detail


def test_loopback_urls_detected():
    assert is_loopback_url("http://127.0.0.1:11434/api/chat")
    assert is_loopback_url("http://localhost:8080/v1")
    assert is_loopback_url("http://[::1]:11434/")
    assert not is_loopback_url("https://api.openai.com/v1")


def test_loopback_requests_ignore_http_proxy(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"models": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # A proxy that refuses everything: going through it would fail.
        for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            monkeypatch.setenv(var, "http://127.0.0.1:9")
        for var in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(var, raising=False)
        url = f"http://127.0.0.1:{server.server_port}/api/tags"
        resp = run(urllib_transport("GET", url, {}, None, 5.0))
        assert resp.status == 200 and resp.json() == {"models": []}
    finally:
        server.shutdown()
