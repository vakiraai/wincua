import pytest

from wincua_mcp.settings import (
    McpSettings,
    SettingsError,
    find_config_path,
    load_settings,
    parse_settings,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.chdir(tmp_path)


def test_defaults_without_config():
    s = load_settings()
    assert s == McpSettings()
    assert s.transport == "stdio"
    assert s.http_port == 8766
    assert s.require_confirmation is True
    assert s.backend == "windows"
    assert s.effective_allow == []


def test_parse_full_mcp_section_and_reads_driver():
    s = parse_settings(
        {
            "driver": {"backend": "mock", "allow": ["msedge.exe", "calc.exe"], "launch_timeout_s": 30},
            "mcp": {
                "transport": "http",
                "http_port": 9000,
                "http_host": "0.0.0.0",
                "allow": ["CalculatorApp.exe", "MSEDGE.EXE"],
                "require_confirmation": False,
                "confirm_timeout_s": 30,
                "max_elements": 50,
            },
            "provider": {"default": "ollama"},
        }
    )
    assert s.transport == "http"
    assert (s.http_host, s.http_port) == ("0.0.0.0", 9000)
    assert s.require_confirmation is False
    assert s.confirm_timeout_s == 30
    assert s.max_elements == 50
    assert s.backend == "mock"
    assert s.launch_timeout_s == 30
    assert s.effective_allow == ["msedge.exe", "calc.exe", "CalculatorApp.exe"]


def test_streamable_http_alias():
    assert parse_settings({"mcp": {"transport": "streamable-http"}}).transport == "http"


@pytest.mark.parametrize(
    "raw",
    [
        {"mcp": {"transport": "sse"}},
        {"mcp": {"http_port": 0}},
        {"mcp": {"http_port": "8766"}},
        {"mcp": {"allow": "calc.exe"}},
        {"mcp": {"require_confirmation": "yes"}},
        {"mcp": "nope"},
        {"driver": {"backend": "cua"}},
    ],
)
def test_invalid_values_raise(raw):
    with pytest.raises(SettingsError):
        parse_settings(raw)


def test_lookup_order(tmp_path, monkeypatch):
    appdata_cfg = tmp_path / "appdata" / "wincua" / "config.toml"
    appdata_cfg.parent.mkdir(parents=True)
    appdata_cfg.write_text('[mcp]\nhttp_port = 1001\n')
    assert find_config_path() == appdata_cfg

    local = tmp_path / "wincua.toml"
    local.write_text('[mcp]\nhttp_port = 1002\n')
    assert load_settings().http_port == 1002

    env_cfg = tmp_path / "env.toml"
    env_cfg.write_text('[mcp]\nhttp_port = 1003\n')
    monkeypatch.setenv("WINCUA_CONFIG", str(env_cfg))
    assert load_settings().http_port == 1003

    explicit = tmp_path / "explicit.toml"
    explicit.write_text('[mcp]\nhttp_port = 1004\n')
    s = load_settings(explicit)
    assert s.http_port == 1004
    assert s.config_path == explicit


def test_missing_explicit_or_env_path_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(SettingsError):
        load_settings(tmp_path / "nope.toml")
    monkeypatch.setenv("WINCUA_CONFIG", str(tmp_path / "nope.toml"))
    with pytest.raises(SettingsError):
        load_settings()


def test_bad_toml(tmp_path):
    p = tmp_path / "wincua.toml"
    p.write_text("[mcp\n")
    with pytest.raises(SettingsError, match="Invalid TOML"):
        load_settings()
