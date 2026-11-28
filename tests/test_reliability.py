"""examples/reliability.py: N demo runs as subprocesses, scored against the pass-rate gate.

The model runs use a fake Ollama serving qwen2.5vl:7b over real HTTP, so each
demo subprocess does its own preflight, warm-up and JSON-reply turns. The fake
answers by turn (assistant messages so far), so every run gets the same script,
and it can abort chosen runs to exercise the gate.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
URL = "__URL__"


def _load():
    spec = importlib.util.spec_from_file_location("reliability", EXAMPLES / "reliability.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rel = _load()

BROWSER = [
    '{"actions": [{"tool": "open_url", "args": {"url": "__URL__", "browser": "msedge", "target": "wincua form demo"}}]}',
    json.dumps({"actions": [
        {"tool": "type", "args": {"element_id": "auto:fullName", "text": "Ada Lovelace"}},
        {"tool": "type", "args": {"element_id": "auto:email", "text": "ada@example.com"}},
        {"tool": "invoke", "args": {"element_id": "auto:submitButton"}},
    ]}),
    '{"actions": [{"tool": "done", "args": {"message": "OK: Ada Lovelace <ada@example.com>"}}]}',
]
CALC = [
    '{"actions": [{"tool": "launch", "args": {"app": "calculator", "title": "Calculator"}}]}',
    json.dumps({"actions": [{"tool": "invoke", "args": {"element_id": f"auto:{b}"}} for b in (
        "clearButton", "num1Button", "num7Button", "plusButton", "num2Button", "num5Button", "equalButton")]}),
    '{"actions": [{"tool": "done", "args": {"message": "42"}}]}',
]
ABORT = '{"actions": [{"tool": "abort", "args": {"reason": "model gave up"}}]}'


class FakeOllama:
    """qwen2.5vl:7b look-alike. Run k = k-th /api/generate warm-up; runs in ``fail_runs`` abort."""

    model = "qwen2.5vl:7b"

    def __init__(self, fail_runs: set[int] = frozenset()) -> None:
        self.fail_runs = set(fail_runs)
        self.runs = 0
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
                    fake.runs += 1
                    return self._send({"model": fake.model, "response": "", "done": True})
                if "tools" in body:
                    return self._send({"error": f"{fake.model} does not support tools"}, 400)
                msgs = body["messages"]
                task = next(m["content"] for m in msgs if m["role"] == "user")
                script = BROWSER if "open_url" in task else CALC
                turn = sum(m["role"] == "assistant" for m in msgs)
                text = ABORT if fake.runs in fake.fail_runs or turn >= len(script) else script[turn]
                if URL in text:
                    text = text.replace(URL, re.search(r"url='([^']+)'", task).group(1))
                self._send({"model": fake.model, "message": {"role": "assistant", "content": text}, "done": True})

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


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WINCUA_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    return tmp_path


def _config(tmp_path: Path, base_url: str) -> str:
    text = (ROOT / "wincua.example.toml").read_text(encoding="utf-8")
    path = tmp_path / "wincua.toml"
    path.write_text(text.replace('base_url = "http://127.0.0.1:11434"', f'base_url = "{base_url}"', 1), encoding="utf-8")
    return str(path)


def _report(out: Path) -> dict:
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


def test_scripted_mock_runs_meet_gate_and_write_reports(workdir, capsys):
    out = workdir / "rep"
    code = rel.main(["--mock", "--provider", "scripted", "--runs", "2", "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 0, text
    assert "gate 80%: PASS" in text and "browser    2/2 passed = 100%" in text
    report = _report(out)
    assert report["meets_gate"] and report["provider"] == "scripted"
    assert [Path(r["command"][1]).name for r in report["runs"]] == ["browser_form_demo.py"] * 2 + ["calculator_arith_demo.py"] * 2
    for r in report["runs"]:
        assert r["status"] == "PASS" and r["duration_s"] > 0 and Path(r["log"]).is_file()
        assert r["recordings"] and r["recordings"][0]["path"].startswith("recordings")
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "**MEETS GATE**" in md and "| browser | 2/2 | 100% | meets |" in md


def test_provider_defaults_to_config_and_one_failure_in_five_still_meets_gate(workdir, capsys):
    out = workdir / "rep"
    with FakeOllama(fail_runs={3}) as fake:
        cfg = _config(workdir, fake.base_url)
        code = rel.main(["--mock", "--config", cfg, "--demos", "browser", "--runs", "5", "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 0, text
    report = _report(out)
    assert report["provider"] == "ollama" and "[provider].default" in report["provider_source"]
    assert [r["status"] for r in report["runs"]] == ["PASS", "PASS", "FAIL", "PASS", "PASS"]
    assert report["summary"]["browser"] == {**report["summary"]["browser"], "passes": 4, "pass_rate": 0.8, "meets_gate": True}
    failed = report["runs"][2]
    assert failed["exit_code"] == 2 and "model gave up" in failed["reason"]
    assert "qwen2.5vl:7b" in Path(failed["log"]).read_text(encoding="utf-8")
    assert "4/5 passed = 80%" in text and "gate 80%: PASS" in text


def test_two_failures_in_five_is_below_gate_and_exits_nonzero(workdir, capsys):
    out = workdir / "rep"
    with FakeOllama(fail_runs={2, 4}) as fake:
        cfg = _config(workdir, fake.base_url)
        code = rel.main(["--mock", "--provider", "ollama", "--config", cfg, "--demos", "calculator",
                         "--runs", "5", "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 1, text
    report = _report(out)
    assert Path(report["runs"][0]["command"][1]).name == "calculator_llm_demo.py"
    assert report["summary"]["calculator"]["passes"] == 3 and not report["meets_gate"]
    assert "3/5 passed = 60%" in text and "BELOW GATE" in text and "gate 80%: FAIL" in text
    assert "**below**" in (out / "report.md").read_text(encoding="utf-8")


def test_unreachable_model_fails_closed_with_the_fallback_reason(workdir, capsys):
    out = workdir / "rep"
    cfg = _config(workdir, "http://127.0.0.1:9")
    code = rel.main(["--mock", "--provider", "ollama", "--config", cfg, "--demos", "browser", "--runs", "1",
                     "--out", str(out)])
    assert code == 1
    run = _report(out)["runs"][0]
    assert run["status"] == "FAIL" and "ollama" in run["fallback"] and "unavailable" in run["fallback"]
    assert run["reason"].startswith(run["fallback"])


def test_run_timeout_kills_the_demo_and_says_so():
    code, output, timed_out = rel.run_process(
        [sys.executable, "-c", "import time; print('loading model', flush=True); time.sleep(30)"], timeout_s=1.5)
    assert timed_out and code is None and "loading model" in output
    reason = rel.failure_reason(output, code, timed_out=True, timeout_s=1.5, pass_seen=False)
    assert reason.startswith("timed out after 2s") and "loading model" in reason


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("HOST-WINDOWS-ONLY: needs Win11\n", "HOST-WINDOWS-ONLY: needs Win11"),
        ("result.ok=True message='done'\ngolden 'OK: x': FAIL (seen {'title': 'y'})\n", "golden 'OK: x': FAIL (seen {'title': 'y'})"),
        ("      -> invoke: FAILED: element not found\nresult.ok=False message='turn limit'\n",
         "agent: 'turn limit' | -> invoke: FAILED: element not found"),
        ("Traceback ...\nRuntimeError: boom\n", "exit code 1: RuntimeError: boom"),
    ],
)
def test_failure_reason_picks_the_most_specific_line(output, expected):
    assert rel.failure_reason(output, 1, timed_out=False, timeout_s=60, pass_seen=False) == expected


def test_more_than_one_human_clarification_fails_the_run(tmp_path):
    out = "  HUMAN: 'press 4' -> ollama\n  HUMAN: 'press 2' -> ollama\nverify: display='42' expected 42 -> PASS\n"
    cmd = [sys.executable, str(EXAMPLES / "calculator_llm_demo.py")]
    r = rel.score_run("calculator", 1, cmd, 0, out, False, 1.0, 60, "x.log", tmp_path)
    assert r.status == "FAIL" and "2 human clarifications" in r.reason
    one = rel.score_run("calculator", 1, cmd, 0, out.split("\n", 1)[1], False, 1.0, 60, "x.log", tmp_path)
    assert one.status == "PASS" and one.human_turns == 1


def test_gate_is_inclusive_and_command_mapping():
    runs = [rel.RunResult("browser", k, "PASS" if k != 5 else "FAIL", 1.0, 0) for k in range(1, 6)]
    assert rel.summarize(runs, ["browser"], 0.8)["browser"]["meets_gate"] is True
    args = rel.parse_args(["--provider", "ollama", "--config", "w.toml", "--timeout", "60", "--max-turns", "10"])
    cmd = rel.demo_command("calculator", "ollama", args, python="py")
    assert cmd[1].endswith("calculator_llm_demo.py") and cmd[2:] == ["--provider", "ollama", "--config", "w.toml",
                                                                     "--timeout", "60.0", "--max-turns", "10"]
    scripted = rel.demo_command("calculator", "scripted", args, python="py")
    assert scripted[1].endswith("calculator_arith_demo.py") and "--config" not in scripted and "--max-turns" not in scripted
    assert rel.close_demo_windows(("wincua form demo",)) == 0 or sys.platform == "win32"
