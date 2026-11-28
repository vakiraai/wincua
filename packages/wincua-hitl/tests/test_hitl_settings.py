import pytest

from wincua_hitl import CliUI, LocalUI, OffUI, from_settings, make_transport
from wincua_hitl.settings import HitlSettings, load_hitl_settings, parse_hitl_settings


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.chdir(tmp_path)


def test_defaults_without_config():
    s = load_hitl_settings()
    assert s == HitlSettings()
    assert (s.mode, s.host, s.port, s.confirm_risky_actions) == ("ui", "127.0.0.1", 8765, True)


def test_parse_section():
    s = parse_hitl_settings(
        {"hitl": {"mode": "CLI", "port": 9000, "confirm_risky_actions": False, "timeout_s": 5}}
    )
    assert (s.mode, s.port, s.confirm_risky_actions, s.timeout_s) == ("cli", 9000, False, 5.0)


def test_parse_accepts_object_with_raw():
    class Cfg:
        raw = {"hitl": {"mode": "off"}}

    assert parse_hitl_settings(Cfg()).mode == "off"


@pytest.mark.parametrize(
    "section",
    [
        {"mode": "web"},
        {"host": "0.0.0.0"},
        {"host": "192.168.1.5"},
        {"port": 70000},
        {"port": "8765"},
        {"timeout_s": 0},
        
    ],
)
def test_invalid_sections_rejected(section):
    with pytest.raises(ValueError):
        parse_hitl_settings({"hitl": section})


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_hosts_allowed(host):
    assert parse_hitl_settings({"hitl": {"host": host}}).host == host


def test_lookup_order(tmp_path, monkeypatch):
    (tmp_path / "appdata" / "wincua").mkdir(parents=True)
    (tmp_path / "appdata" / "wincua" / "config.toml").write_text('[hitl]\nport = 1001\n')
    assert load_hitl_settings().port == 1001

    (tmp_path / "wincua.toml").write_text('[hitl]\nport = 1002\n')
    assert load_hitl_settings().port == 1002

    env = tmp_path / "env.toml"
    env.write_text('[hitl]\nport = 1003\n')
    monkeypatch.setenv("WINCUA_CONFIG", str(env))
    assert load_hitl_settings().port == 1003

    explicit = tmp_path / "explicit.toml"
    explicit.write_text('[provider]\ndefault = "null"\n[hitl]\nport = 1004\n')
    s = load_hitl_settings(explicit)
    assert s.port == 1004 and s.source == str(explicit)


def test_missing_explicit_path_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_hitl_settings(tmp_path / "nope.toml")


def test_factory_modes():
    assert isinstance(from_settings(HitlSettings(mode="ui")), LocalUI)
    assert isinstance(from_settings(HitlSettings(mode="cli")), CliUI)
    assert isinstance(from_settings(HitlSettings(mode="off")), OffUI)
    assert make_transport(None) is None
    assert isinstance(make_transport("cli"), CliUI)
    assert isinstance(make_transport("auto"), LocalUI)
    with pytest.raises(ValueError):
        make_transport("telepathy")


def test_local_ui_refuses_non_loopback():
    with pytest.raises(ValueError):
        LocalUI(host="0.0.0.0")


def test_unknown_keys_warn_not_fail(capsys):
    assert parse_hitl_settings({"hitl": {"mode": "cli", "colour": "blue"}}).mode == "cli"
    assert "colour" in capsys.readouterr().err
