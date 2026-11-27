"""Agent with model providers from config (mock desktop + fake HTTP)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "wincua-providers" / "tests"))

from fake_http import FakeTransport, ollama_tool_reply, openai_tool_reply  # noqa: E402
from wincua import Agent  # noqa: E402
from wincua.config import parse_config  # noqa: E402
from wincua_driver.backend import get_backend, set_backend  # noqa: E402
from wincua_providers import NullProvider  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.chdir(tmp_path)
    set_backend("mock")


def _calc_agent(provider, **kw) -> Agent:
    return Agent(
        task="compute 17 + 25",
        provider=provider,
        allow=["CalculatorApp.exe"],
        backend="mock",
        observe_target="Calculator",
        **kw,
    )


def test_ollama_plans_calculator_on_mock():
    t = FakeTransport()
    t.add(200, ollama_tool_reply(("launch", {"app": "calculator", "title": "Calculator"})))
    first = ("clearButton", "num1Button", "num7Button", "plusButton")
    t.add(200, ollama_tool_reply(*[("invoke", {"auto_id": a}) for a in first]))
    t.add(
        200,
        ollama_tool_reply(
            ("invoke", {"auto_id": "num2Button"}),
            ("invoke", {"name": "x", "auto_id": "num5Button"}),
            ("invoke", {"auto_id": "equalButton"}),
        ),
    )
    t.add(200, ollama_tool_reply(("done", {"message": "42 is shown"})))
    cfg = parse_config({"provider": {"default": "ollama"}})
    agent = _calc_agent(cfg.build_provider(transport=t))

    result = asyncio.run(agent.run())
    assert result.ok, result.message
    assert result.message == "42 is shown"
    assert get_backend().display == "42"
    plans = [s for s in result.steps if s["kind"] == "plan"]
    assert {p["provider"] for p in plans} == {"ollama"}
    # The model saw the results of its earlier calls.
    last_msgs = t.requests[-1].body["messages"]
    assert sum(1 for m in last_msgs if m["role"] == "tool") == 8
    assert "Display is 42" in last_msgs[-1]["content"]


def test_tool_error_is_fed_back_to_model():
    t = FakeTransport()
    t.add(200, openai_tool_reply(("teleport", {})))
    t.add(200, openai_tool_reply(("done", {"message": "recovered"})))
    cfg = parse_config({"provider": {"default": "llamacpp"}})
    result = asyncio.run(_calc_agent(cfg.build_provider(transport=t)).run())
    assert result.ok and result.message == "recovered"
    tool_msgs = [m for m in t.requests[1].body["messages"] if m["role"] == "tool"]
    assert "Unknown tool" in tool_msgs[0]["content"]


def test_text_only_replies_get_a_nudge_then_give_up():
    t = FakeTransport()
    for _ in range(3):
        t.add(200, {"message": {"role": "assistant", "content": "thinking..."}})
    cfg = parse_config({"provider": {"default": "ollama"}})
    agent = _calc_agent(cfg.build_provider(transport=t), max_consecutive_errors=3)
    result = asyncio.run(agent.run())
    assert not result.ok and "no tool call 3 times" in result.message
    assert "Reply with a tool call" in str(t.requests[1].body["messages"])


def test_unreachable_default_falls_back_through_chain():
    dead = FakeTransport().fail(ConnectionError("refused"))
    cfg = parse_config({"provider": {"default": "ollama", "fallback": ["null"]}})
    agent = _calc_agent(cfg.build_provider(transport=dead))
    result = asyncio.run(agent.run())
    assert not result.ok and "HITL" in result.message
    plan = [s for s in result.steps if s["kind"] == "plan"][0]
    assert plan["provider"] == "null" and "ollama unavailable" in plan["error"]


def test_unreachable_falls_back_to_next_model():
    t = FakeTransport().fail(ConnectionError("refused")).add(200, openai_tool_reply(("done", {"message": "via llama"})))
    cfg = parse_config({"provider": {"default": "ollama", "fallback": ["llamacpp", "null"]}})
    result = asyncio.run(_calc_agent(cfg.build_provider(transport=t)).run())
    assert result.ok and result.message == "via llama"
    assert t.requests[1].url.startswith("http://127.0.0.1:8080/v1/")


def test_agent_builds_provider_from_config_file(tmp_path):
    (tmp_path / "wincua.toml").write_text(
        '[provider]\ndefault = "ollama"\nfallback = ["null"]\n'
        '[providers.ollama]\nbase_url = "http://127.0.0.1:9"\n'
        '[driver]\nallow = ["CalculatorApp.exe", "calc.exe"]\n',
        encoding="utf-8",
    )
    agent = Agent(task="t", allow=["calc.exe"], backend="mock")
    assert agent.provider.chain == ["ollama", "null"]
    assert agent.provider.providers[0].base_url == "http://127.0.0.1:9"
    assert list(agent.allow) == ["calc.exe", "CalculatorApp.exe"]


def test_agent_provider_name_overrides_default(tmp_path):
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[provider]\ndefault = "ollama"\n', encoding="utf-8")
    agent = Agent(task="t", provider="anthropic", config=cfg_path, backend="mock")
    assert agent.provider.chain == ["anthropic", "null"]


def test_agent_without_config_or_provider_is_null():
    agent = Agent(task="t", backend="mock")
    assert isinstance(agent.provider, NullProvider)
