"""The two failures of the live Win11 reliability run (28 Nov 2026, qwen2.5vl:7b), unattended.

1. Browser: the model "launched" msedge instead of calling open_url, the
   allow-list (msedge.exe) refused the bare "msedge", and the run fell back to
   a human who was not there.
2. Calculator: the model re-read the task each turn and pressed Clear again
   (temperature 0, so the same prompt gave the same reply) until the loop guard
   escalated to the human.

The fake below answers like that model: it reacts to what the prompt says, not
to a turn script. The MockBackend enforces the allow-list like WindowsBackend.
Control cases switch the fixes off and must reproduce the live failures.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
SIX = ("num1Button", "num7Button", "plusButton", "num2Button", "num5Button", "equalButton")


def _actions(*calls) -> str:
    return json.dumps({"actions": [{"tool": t, "args": a} for t, a in calls]})


class LiveLikeQwen:
    """qwen2.5vl:7b in JSON mode, reproducing the live behaviour.

    browser: "launch_url" = launch(app='msedge', url=…); "launch_bare" = launch(app='msedge')
    until told open_url needs a url. calculator: "restart" = presses only Clear unless the prompt
    says Clear already succeeded; "stubborn" = presses only Clear until the loop-guard warning.
    """

    model = "qwen2.5vl:7b"

    def __init__(self, browser: str = "launch_url", calculator: str = "restart") -> None:
        self.browser, self.calculator = browser, calculator
        self.chats = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, obj: dict, code: int = 200) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                self._send({"models": [{"name": fake.model}]})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/generate":
                    return self._send({"model": fake.model, "response": "", "done": True})
                if "tools" in body:
                    return self._send({"error": f"{fake.model} does not support tools"}, 400)
                fake.chats += 1
                text = fake.reply(body["messages"])
                self._send({"model": fake.model, "message": {"role": "assistant", "content": text}, "done": True})

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def reply(self, msgs: list[dict]) -> str:
        task = next(m["content"] for m in msgs if m["role"] == "user")
        last = msgs[-1]["content"]
        convo = "\n".join(m["content"] for m in msgs if m["role"] == "user")
        if "open_url" in task:
            return self._browser(task, last, convo)
        return self._calculator(last, convo)

    def _browser(self, task: str, last: str, convo: str) -> str:
        url = re.search(r"url='([^']+)'", task).group(1)
        if "form.html#submitted" in last:
            return _actions(("done", {"message": "OK: Ada Lovelace <ada@example.com>"}))
        if re.search(r"\d\. open_url: ok", convo):
            return _actions(
                ("type", {"element_id": "auto:fullName", "text": "Ada Lovelace"}),
                ("type", {"element_id": "auto:email", "text": "ada@example.com"}),
                ("invoke", {"element_id": "auto:submitButton"}),
            )
        if self.browser == "launch_bare" and "open_url needs url" not in convo:
            return _actions(("launch", {"app": "msedge"}))
        return _actions(("launch", {"app": "msedge", "url": url, "title": "wincua form demo"}))

    def _calculator(self, last: str, convo: str) -> str:
        if '"display": "Display is 42"' in last:
            return _actions(("done", {"message": "42"}))
        if not re.search(r"\d\. launch: ok", convo):
            return _actions(("launch", {"app": "calculator", "title": "Calculator"}))
        told = (
            "Do not send them again" in convo
            if self.calculator == "stubborn"
            else "Already succeeded" in last and 'invoke(element_id="auto:clearButton")' in last
        )
        if told:
            return _actions(*[("invoke", {"element_id": f"auto:{b}"}) for b in SIX])
        return _actions(("invoke", {"element_id": "auto:clearButton"}))

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _load(name: str):
    sys.path.insert(0, str(EXAMPLES))
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # unattended: no TTY, a human fallback fails closed

    def write(base_url: str) -> str:
        text = (ROOT / "wincua.example.toml").read_text(encoding="utf-8")
        path = tmp_path / "wincua.toml"
        path.write_text(text.replace('base_url = "http://127.0.0.1:11434"', f'base_url = "{base_url}"', 1), encoding="utf-8")
        return str(path)

    return write


def _demo(name: str, cfg: str, capsys) -> tuple[int, str]:
    code = asyncio.run(_load(name).main(["--mock", "--provider", "ollama", "--config", cfg]))
    return code, capsys.readouterr().out


@pytest.mark.parametrize("browser", ["launch_url", "launch_bare"])
def test_browser_launch_msedge_reaches_golden_without_a_human(browser, config, capsys):
    with LiveLikeQwen(browser=browser) as fake:
        code, out = _demo("browser_form_demo", config(fake.base_url), capsys)
    assert code == 0, out
    assert "golden 'OK: Ada Lovelace <ada@example.com>': PASS" in out
    assert "allow-list" not in out and "ESCALATE" not in out and "HITL unavailable" not in out
    assert "[turn 0] ollama: open_url(browser='msedge'" in out  # the model's launch ran as open_url
    if browser == "launch_bare":
        assert "open_url: FAILED: open_url needs url" in out


def test_control_browser_launch_is_refused_without_the_alias(config, capsys, monkeypatch):
    import wincua_providers.tools as tools

    monkeypatch.setattr(tools, "browser_launch_as_open_url", lambda args: None)
    with LiveLikeQwen() as fake:
        code, out = _demo("browser_form_demo", config(fake.base_url), capsys)
    # A bare Edge opens no page, so the model launches again and again until the guard gives up.
    assert code != 0
    assert "[turn 0] ollama: launch(app='msedge'" in out and "ESCALATE" in out
    assert "HITL unavailable / no answer (fail-closed)" in out
    assert "golden 'OK: Ada Lovelace <ada@example.com>': FAIL" in out


@pytest.mark.parametrize("calculator", ["restart", "stubborn"])
def test_calculator_clear_loop_reaches_42_without_escalating(calculator, config, capsys):
    with LiveLikeQwen(calculator=calculator) as fake:
        code, out = _demo("calculator_llm_demo", config(fake.base_url), capsys)
    assert code == 0, out
    assert "verify: display='Display is 42' expected 42 -> PASS" in out
    assert "ESCALATE" not in out and "HITL unavailable" not in out
    presses = re.findall(r"invoke\(element_id='auto:(\w+)'", out)
    expected_clears = 1 if calculator == "restart" else 2
    assert presses == ["clearButton"] * expected_clears + list(SIX)
    if calculator == "stubborn":
        assert "LOOP WARNING (sent to the model): You sent the same actions 2 turns in a row" in out


def test_control_calculator_without_progress_or_warning_escalates_like_live(config, capsys, monkeypatch):
    import wincua.guard as guard
    import wincua_providers.ollama as ollama

    monkeypatch.setattr(ollama, "progress_text", lambda turns: "")
    real_end_turn = guard.LoopGuard.end_turn

    def no_warning(self, fingerprint, signatures):
        stuck = real_end_turn(self, fingerprint, signatures)
        self.warning = None
        return stuck

    monkeypatch.setattr(guard.LoopGuard, "end_turn", no_warning)
    with LiveLikeQwen(calculator="restart") as fake:
        code, out = _demo("calculator_llm_demo", config(fake.base_url), capsys)
    assert code != 0
    assert "ESCALATE: same actions repeated 3 turns in a row" in out and "auto:clearButton" in out
    assert "HITL unavailable / no answer (fail-closed)" in out


def test_reliability_unattended_passes_5_of_5_for_both_demos(config, tmp_path):
    rel = _load("reliability")
    out = tmp_path / "rep"
    with LiveLikeQwen(browser="launch_bare", calculator="restart") as fake:
        cfg = config(fake.base_url)
        code = rel.main(["--mock", "--provider", "ollama", "--config", cfg, "--runs", "5", "--out", str(out)])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert code == 0, [r["reason"] for r in report["runs"]]
    assert {d: (s["passes"], s["runs"]) for d, s in report["summary"].items()} == {"browser": (5, 5), "calculator": (5, 5)}
    for r in report["runs"]:
        log = Path(r["log"]).read_text(encoding="utf-8")
        assert "ESCALATE" not in log and "HUMAN:" not in log and "fail-closed" not in log
