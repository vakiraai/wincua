"""wincua.config: lookup order, validation, provider chain."""

from __future__ import annotations

from pathlib import Path

import pytest
from wincua.config import ConfigError, find_config_path, load_config, parse_config
from wincua_providers import (
    AnthropicProvider,
    FallbackProvider,
    LlamaCppProvider,
    NullProvider,
    OllamaProvider,
    OpenAICompatProvider,
)

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.chdir(tmp_path)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_no_file_means_null_provider():
    cfg = load_config()
    assert cfg.path is None and cfg.provider.default == "null"
    assert cfg.chain() == ["null"]
    assert isinstance(cfg.build_provider(), NullProvider)


def test_lookup_order(tmp_path, monkeypatch):
    appdata = write(tmp_path / "appdata" / "wincua" / "config.toml", '[provider]\ndefault = "openai"\n')
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert find_config_path() == appdata

    local = write(tmp_path / "wincua.toml", '[provider]\ndefault = "llamacpp"\n')
    assert find_config_path() == local

    env = write(tmp_path / "env.toml", '[provider]\ndefault = "anthropic"\n')
    monkeypatch.setenv("WINCUA_CONFIG", str(env))
    assert find_config_path() == env

    explicit = write(tmp_path / "explicit.toml", '[provider]\ndefault = "ollama"\n')
    assert find_config_path(explicit) == explicit
    assert load_config(explicit).provider.default == "ollama"
    assert load_config().provider.default == "anthropic"


def test_missing_explicit_or_env_file_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")
    monkeypatch.setenv("WINCUA_CONFIG", str(tmp_path / "gone.toml"))
    with pytest.raises(ConfigError, match="WINCUA_CONFIG"):
        load_config()


def test_example_file_parses_and_builds_chain():
    cfg = load_config(REPO / "wincua.example.toml")
    assert cfg.provider.default == "ollama" and cfg.chain() == ["ollama", "null"]
    assert cfg.driver.backend == "windows" and cfg.driver.launch_timeout_s == 45
    assert {"CalculatorApp.exe", "ApplicationFrameHost.exe", "calc.exe"} <= set(cfg.driver.allow)
    assert cfg.raw["mcp"]["max_elements"] == 150 and cfg.raw["hitl"]["port"] == 8765
    assert cfg.raw["hitl"]["timeout_s"] == 300 and cfg.raw["hitl"]["open_browser"] is True
    chain = cfg.build_provider()
    assert isinstance(chain, FallbackProvider) and chain.chain == ["ollama", "null"]
    ollama = chain.providers[0]
    assert isinstance(ollama, OllamaProvider) and ollama.model == "qwen2.5vl:7b"
    assert cfg.provider_config("anthropic").api_key_env == "ANTHROPIC_API_KEY"


def test_override_and_fallback_chain_always_ends_in_null():
    cfg = parse_config(
        {
            "provider": {"default": "ollama", "fallback": ["llamacpp", "anthropic"]},
            "providers": {"llamacpp": {"base_url": "http://127.0.0.1:9000"}},
        }
    )
    assert cfg.chain() == ["ollama", "llamacpp", "anthropic", "null"]
    assert cfg.chain("anthropic") == ["anthropic", "llamacpp", "null"]
    chain = cfg.build_provider()
    types = [type(p) for p in chain.providers]
    assert types == [OllamaProvider, LlamaCppProvider, AnthropicProvider, NullProvider]
    assert chain.providers[1].base_url == "http://127.0.0.1:9000/v1"


def test_null_in_fallback_ends_the_chain():
    cfg = parse_config({"provider": {"default": "ollama", "fallback": ["null", "openai"]}})
    assert cfg.chain() == ["ollama", "null"]


def test_api_keys_are_refused_in_the_file():
    with pytest.raises(ConfigError, match="api_key_env"):
        parse_config({"providers": {"openai": {"api_key": "sk-123"}}})


def test_unknown_provider_reference():
    with pytest.raises(ConfigError, match="unknown provider 'gemini'"):
        parse_config({"provider": {"default": "gemini"}})


def test_custom_named_provider_with_type(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_KEY", "lm")
    cfg = parse_config(
        {
            "provider": {"default": "lmstudio"},
            "providers": {
                "lmstudio": {
                    "type": "openai_compat",
                    "base_url": "http://127.0.0.1:1234/v1",
                    "model": "qwen2.5-vl",
                    "api_key_env": "LMSTUDIO_KEY",
                    "timeout_s": 30,
                }
            },
        }
    )
    p = cfg.build_provider().providers[0]
    assert isinstance(p, OpenAICompatProvider) and p.base_url == "http://127.0.0.1:1234/v1"
    assert p.api_key == "lm" and p.timeout_s == 30


def test_bad_driver_backend():
    with pytest.raises(ConfigError, match="backend"):
        parse_config({"driver": {"backend": "linux"}})


def test_invalid_toml_names_the_file(tmp_path):
    bad = write(tmp_path / "bad.toml", "[provider\n")
    with pytest.raises(ConfigError, match="bad.toml"):
        load_config(bad)


def test_provider_options_pass_through():
    cfg = parse_config({"provider": {"default": "anthropic"}, "providers": {"anthropic": {"computer_use": True}}})
    assert cfg.build_provider().providers[0].computer_use is True
