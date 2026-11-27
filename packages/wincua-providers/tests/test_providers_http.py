"""Provider wire formats against a fake HTTP transport (no real APIs)."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fake_http import (
    FakeTransport,
    PNG_1x1,
    anthropic_tool_reply,
    ollama_tool_reply,
    openai_tool_reply,
)
from wincua_driver.types import ElementRef, ImageRef, Observation
from wincua_providers import (
    AnthropicProvider,
    FallbackProvider,
    LlamaCppProvider,
    NullProvider,
    OllamaProvider,
    OpenAICompatProvider,
    OpenAIProvider,
    build_provider,
)
from wincua_providers.tools import parse_json_tool_calls


def run(coro):
    return asyncio.run(coro)


def calc_obs(**kw) -> Observation:
    return Observation(
        uia=[
            ElementRef("win", role="Window", name="Calculator", bounds=(100, 50, 420, 600)),
            ElementRef("auto:num1Button", role="Button", name="One", patterns=("Invoke",)),
        ],
        screenshot=ImageRef(data=PNG_1x1, capture_quality="full"),
        app={"target": "Calculator", "display": "Display is 0"},
        **kw,
    )


HISTORY = [
    {"kind": "tool", "name": "launch", "args": {"app": "calculator"}, "call_id": "c1", "turn": 0,
     "result": {"ok": True}},
    {"kind": "tool", "name": "invoke", "args": {"auto_id": "num1Button"}, "call_id": "c2", "turn": 1,
     "result": {"ok": False, "message": "not found"}},
]


# -- Ollama ------------------------------------------------------------------------


def test_ollama_native_chat_with_image_and_tools():
    t = FakeTransport().add(200, ollama_tool_reply(("invoke", {"auto_id": "num1Button"}), ("done", {"message": "42"})))
    p = OllamaProvider(model="qwen2.5vl:7b", transport=t)
    res = run(p.plan(task="calc", observation=calc_obs(), history=[]))
    assert res.available and res.provider == "ollama"
    assert [(s.kind, s.name) for s in res.steps] == [("tool", "invoke"), ("done", "done")]
    assert res.steps[0].args == {"auto_id": "num1Button"}
    assert res.steps[1].message == "42"
    req = t.requests[0]
    assert req.url == "http://127.0.0.1:11434/api/chat"
    assert req.body["model"] == "qwen2.5vl:7b" and req.body["stream"] is False
    assert {tool["function"]["name"] for tool in req.body["tools"]} >= {"invoke", "type", "done"}
    last = req.body["messages"][-1]
    assert last["role"] == "user" and last["images"] and "auto:num1Button" in last["content"]
    assert "Task: calc" in last["content"]


def test_ollama_history_renders_tool_messages():
    t = FakeTransport().add(200, ollama_tool_reply(("done", {"message": "ok"})))
    p = OllamaProvider(transport=t)
    run(p.plan(task="calc", observation=calc_obs(), history=HISTORY))
    msgs = t.requests[0].body["messages"]
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "tool", "user"]
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "launch", "arguments": {"app": "calculator"}}
    assert msgs[5]["content"].startswith("FAILED: not found")


def test_ollama_falls_back_to_json_mode_when_model_lacks_tools():
    t = (
        FakeTransport()
        .add(400, {"error": "registry.ollama.ai/library/qwen2.5vl:7b does not support tools"})
        .add(200, {"message": {"role": "assistant", "content": '{"tool": "launch", "args": {"app": "calculator"}}'}})
    )
    p = OllamaProvider(transport=t)
    res = run(p.plan(task="calc", observation=calc_obs()))
    assert res.available and res.steps[0].name == "launch"
    assert p.tool_mode == "json"
    second = t.requests[1].body
    assert "tools" not in second and second["format"] == "json"
    assert "Respond with ONLY a JSON object" in second["messages"][0]["content"]


def test_ollama_missing_model_is_unavailable_with_pull_hint():
    t = FakeTransport().add(404, {"error": "model 'x' not found"})
    res = run(OllamaProvider(model="x", transport=t).plan(task="t"))
    assert not res.available and "ollama pull x" in res.error


def test_ollama_unreachable_is_unavailable():
    t = FakeTransport().fail(ConnectionError("connection refused"))
    res = run(OllamaProvider(transport=t).plan(task="t"))
    assert not res.available and "unreachable" in res.error and not res.steps


def test_ollama_text_reply_without_tool_call():
    t = FakeTransport().add(200, {"message": {"role": "assistant", "content": "I will open it."}})
    res = run(OllamaProvider(transport=t).plan(task="t"))
    assert res.available and not res.steps and res.text == "I will open it."


def test_ollama_probe_checks_model_is_pulled():
    t = FakeTransport().add(200, {"models": [{"name": "qwen2.5vl:7b"}]}).add(200, {"models": []})
    p = OllamaProvider(transport=t)
    assert run(p.probe())[0] is True
    ok, detail = run(p.probe())
    assert not ok and "ollama pull" in detail


def test_retry_on_503_then_success():
    t = FakeTransport().add(503, {"error": "loading"}).add(200, ollama_tool_reply(("done", {"message": "x"})))
    res = run(OllamaProvider(transport=t, retry_backoff_s=0).plan(task="t"))
    assert res.available and len(t.requests) == 2


# -- OpenAI-compatible / llama.cpp / OpenAI ------------------------------------------


def test_openai_compat_payload_and_parsing():
    t = FakeTransport().add(200, openai_tool_reply(("type", {"text": "17", "auto_id": "x"})))
    p = OpenAICompatProvider(model="m", base_url="http://127.0.0.1:1234/v1", api_key="k", transport=t)
    res = run(p.plan(task="calc", observation=calc_obs(), history=HISTORY))
    assert res.steps[0].name == "type" and res.steps[0].args["text"] == "17"
    assert res.steps[0].call_id == "call_0"
    req = t.requests[0]
    assert req.url == "http://127.0.0.1:1234/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer k"
    msgs = req.body["messages"]
    assert msgs[2]["tool_calls"][0]["id"] == "c1"
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}
    content = msgs[-1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert req.body["tool_choice"] == "auto"


def test_llamacpp_appends_v1_and_drops_images_without_mmproj():
    t = (
        FakeTransport()
        .add(500, {"error": {"message": "image input is not supported - hint: you may need to provide the mmproj"}})
        .add(200, openai_tool_reply(("done", {"message": "ok"})))
    )
    p = LlamaCppProvider(transport=t)
    res = run(p.plan(task="t", observation=calc_obs()))
    assert res.available and res.provider == "llamacpp"
    assert t.requests[0].url == "http://127.0.0.1:8080/v1/chat/completions"
    assert isinstance(t.requests[1].body["messages"][-1]["content"], str)
    assert p.send_screenshot is False


def test_llamacpp_without_jinja_uses_json_mode():
    t = (
        FakeTransport()
        .add(500, {"error": {"message": "tools param requires --jinja flag"}})
        .add(200, {"choices": [{"message": {"content": '```json\n{"tool": "key", "args": {"keys": "enter"}}\n```'}}]})
    )
    res = run(LlamaCppProvider(transport=t).plan(task="t"))
    assert res.steps[0].name == "key" and res.steps[0].args == {"keys": "enter"}
    assert "tools" not in t.requests[1].body


def test_llamacpp_probe_uses_health():
    t = FakeTransport().add(200, {"status": "ok"})
    assert run(LlamaCppProvider(transport=t).probe())[0]
    assert t.requests[0].url == "http://127.0.0.1:8080/health"


def test_openai_missing_key_is_unavailable_without_http(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    t = FakeTransport()
    res = run(OpenAIProvider(transport=t).plan(task="t"))
    assert not res.available and "OPENAI_API_KEY" in res.error and not t.requests


def test_openai_uses_env_key_and_default_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    t = FakeTransport().add(200, openai_tool_reply(("done", {"message": "ok"})))
    run(OpenAIProvider(max_tokens=300, transport=t).plan(task="t"))
    req = t.requests[0]
    assert req.url == "https://api.openai.com/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer sk-test"
    assert req.body["model"] == "gpt-4.1" and req.body["max_completion_tokens"] == 300


def test_auth_error_is_unavailable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "bad")
    t = FakeTransport().add(401, {"error": {"message": "invalid key"}})
    res = run(OpenAIProvider(transport=t).plan(task="t"))
    assert not res.available and "401" in res.error


# -- Anthropic -----------------------------------------------------------------------


def test_anthropic_messages_tool_use(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    t = FakeTransport().add(200, anthropic_tool_reply(("invoke", {"auto_id": "num1Button"}), text="Pressing 1"))
    p = AnthropicProvider(transport=t)
    res = run(p.plan(task="calc", observation=calc_obs(), history=HISTORY))
    assert res.steps[0].name == "invoke" and res.steps[0].call_id == "toolu_0"
    assert res.text == "Pressing 1"
    req = t.requests[0]
    assert req.url == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "ak" and req.headers["anthropic-version"] == "2023-06-01"
    assert "anthropic-beta" not in req.headers
    body = req.body
    assert body["model"] == "claude-sonnet-4-5" and body["max_tokens"] == 1024
    assert all(t_.get("type") is None for t_ in body["tools"])
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert body["messages"][1]["content"][0] == {
        "type": "tool_use", "id": "c1", "name": "launch", "input": {"app": "calculator"}
    }
    last = body["messages"][-1]["content"]
    assert last[0] == {"type": "tool_result", "tool_use_id": "c2", "content": "FAILED: not found"}
    assert last[-1]["type"] == "image" and last[-2]["type"] == "text"


def test_anthropic_computer_use_maps_to_screen_coordinates(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    t = FakeTransport().add(
        200,
        anthropic_tool_reply(
            ("computer", {"action": "left_click", "coordinate": [10, 20]}),
            ("computer", {"action": "key", "text": "Return"}),
            ("computer", {"action": "scroll", "coordinate": [1, 1]}),
        ),
    )
    p = AnthropicProvider(computer_use=True, transport=t)
    res = run(p.plan(task="t", observation=calc_obs()))
    req = t.requests[0]
    assert req.headers["anthropic-beta"] == "computer-use-2025-01-24"
    cu = [x for x in req.body["tools"] if x.get("name") == "computer"][0]
    assert cu["type"] == "computer_20250124" and cu["display_width_px"] == 1
    click, key, scroll = res.steps
    assert (click.name, click.args["x"], click.args["y"]) == ("click", 110, 70)
    assert key.args["keys"] == "enter"
    assert scroll.name == "computer.scroll"
    history = [
        {"kind": "tool", "name": s.name, "args": s.args, "call_id": s.call_id, "turn": 0, "result": {"ok": True}}
        for s in res.steps
    ]
    t.add(200, anthropic_tool_reply(("done", {"message": "ok"})))
    run(p.plan(task="t", observation=calc_obs(), history=history))
    blocks = t.requests[1].body["messages"][1]["content"]
    assert blocks[0] == {"type": "tool_use", "id": "toolu_0", "name": "computer",
                         "input": {"action": "left_click", "coordinate": [10, 20]}}


def test_anthropic_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = run(AnthropicProvider(transport=FakeTransport()).plan(task="t"))
    assert not res.available and "ANTHROPIC_API_KEY" in res.error


# HITL-style entries (no turn / call_id; not tool calls) must not become bogus tool calls.
HITL_HISTORY = [
    {"kind": "human_instruction", "text": "use the keypad"},
    {"kind": "tool", "name": "key", "args": {"keys": "alt+f4"}, "call_id": "k1", "turn": 0},
    {"kind": "rejected", "name": "key", "args": {"keys": "alt+f4"}},
    {"kind": "tool_failed", "name": "invoke", "message": "boom"},
]


def test_hitl_history_entries_render_as_notes(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    tools = {"key", "invoke", "type", "launch", "done"}

    t = FakeTransport().add(200, openai_tool_reply(("done", {"message": "ok"})))
    run(OpenAICompatProvider(transport=t).plan(task="t", observation=calc_obs(), history=HITL_HISTORY))
    msgs = t.requests[0].body["messages"]
    calls = [c["function"]["name"] for m in msgs for c in m.get("tool_calls") or []]
    assert calls == ["key"]
    tool_msg = next(m for m in msgs if m["role"] == "tool")["content"]
    assert tool_msg.startswith("not executed") and "rejected key" in tool_msg and "invoke failed: boom" in tool_msg
    assert "Human instruction: use the keypad" in str(msgs)

    t = FakeTransport().add(200, anthropic_tool_reply(("done", {"message": "ok"})))
    run(AnthropicProvider(transport=t).plan(task="t", observation=calc_obs(), history=HITL_HISTORY))
    amsgs = t.requests[0].body["messages"]
    roles = [m["role"] for m in amsgs]
    assert all(a != b for a, b in zip(roles, roles[1:])) and roles[0] == "user"
    names = {b["name"] for m in amsgs for b in m["content"] if b.get("type") == "tool_use"}
    assert names <= tools

    t = FakeTransport().add(200, ollama_tool_reply(("done", {"message": "ok"})))
    only_note = [{"kind": "human_instruction", "text": "press 7 first"}]
    run(OllamaProvider(transport=t).plan(task="t", observation=calc_obs(), history=only_note))
    omsgs = t.requests[0].body["messages"]
    assert [m["role"] for m in omsgs] == ["system", "user", "user"]
    assert "Human instruction: press 7 first" in omsgs[-1]["content"]


# -- fallback / registry / parsing ---------------------------------------------------


def test_fallback_moves_on_when_unreachable_and_stays():
    dead = FakeTransport().fail(ConnectionError("refused"))
    live = FakeTransport().add(200, openai_tool_reply(("done", {"message": "a"})))
    live.add(200, openai_tool_reply(("done", {"message": "b"})))
    chain = FallbackProvider(
        [OllamaProvider(transport=dead), LlamaCppProvider(transport=live), NullProvider()]
    )
    res = run(chain.plan(task="t"))
    assert res.available and res.provider == "llamacpp" and chain.name == "llamacpp"
    assert "ollama unavailable" in res.error and "falling back to llamacpp" in res.error
    res2 = run(chain.plan(task="t"))
    assert res2.steps[0].message == "b" and len(dead.requests) == 1


def test_fallback_ends_in_null_asking_the_human():
    chain = FallbackProvider(
        [OllamaProvider(transport=FakeTransport().fail(ConnectionError("refused"))), NullProvider()]
    )
    res = run(chain.plan(task="t"))
    assert not res.available and res.provider == "null"
    assert res.steps[0].kind == "ask_human"
    assert chain.recovers_from_errors is False


def test_build_provider_by_type():
    p = build_provider("llamacpp", {"base_url": "http://h:9", "model": "", "timeout_s": 5})
    assert isinstance(p, LlamaCppProvider) and p.base_url == "http://h:9/v1" and p.timeout_s == 5
    assert build_provider("null").name == "null"
    with pytest.raises(ValueError):
        build_provider("nope")


@pytest.mark.parametrize(
    "text",
    [
        '{"tool": "invoke", "args": {"name": "Seven"}}',
        'Sure! {"name": "invoke", "arguments": "{\\"name\\": \\"Seven\\"}"}',
        '[{"function": {"name": "invoke", "arguments": {"name": "Seven"}}}]',
    ],
)
def test_parse_json_tool_calls_variants(text):
    assert parse_json_tool_calls(text) == [("invoke", {"name": "Seven"})]


def test_parse_json_ignores_unknown_tools():
    assert parse_json_tool_calls('{"tool": "rm_rf", "args": {}}') == []


# -- real urllib transport against a local stub server -------------------------------


class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append((self.path, body))
        out = json.dumps(ollama_tool_reply(("done", {"message": "stub"}))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


def test_urllib_transport_against_local_stub_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        res = run(OllamaProvider(base_url=f"http://127.0.0.1:{port}").plan(task="t"))
        assert res.available and res.steps[0].message == "stub"
        assert _Handler.seen[-1][0] == "/api/chat"
    finally:
        server.shutdown()
        server.server_close()


def test_urllib_transport_connection_refused_is_unavailable():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    res = run(OllamaProvider(base_url=f"http://127.0.0.1:{port}", timeout_s=3).plan(task="t"))
    assert not res.available and "unreachable" in res.error
