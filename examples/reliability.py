#!/usr/bin/env python3
"""Reliability gate (Phase 1): run the browser and Calculator demos N times and score them.

Each run is a fresh ``python examples/<demo>.py`` process. The harness records
PASS/FAIL, duration, a one-line failure reason and the recording / UIA dump
paths the demo printed, then prints a table with the pass rate against the
gate (default 80%, i.e. at least 4 of 5) and writes ``report.json`` and
``report.md`` with one log per run under ``recordings/reliability-<time>/``.

    python examples/reliability.py                                # provider from wincua.toml, 5 runs each
    python examples/reliability.py --provider ollama              # qwen2.5vl:7b from the example config
    python examples/reliability.py --provider scripted            # no model: scripted demos (driver baseline)
    python examples/reliability.py --demos browser --runs 20      # the section 8 gate: 20 runs
    python examples/reliability.py --mock --provider scripted     # any OS, fake desktop

``--provider scripted`` runs ``browser_form_demo.py`` scripted and
``calculator_arith_demo.py``; any other provider runs ``browser_form_demo.py``
and ``calculator_llm_demo.py`` with ``--provider``. Runs are unattended: the
demo's stdin is closed, so a model that falls back to the human fails closed
(``--allow-human`` lets you answer; a pass may use at most one clarification).
On Windows, demo windows left by the previous run are closed first
(``--keep-windows`` to skip). Exit code: 0 when every demo meets the gate,
1 when one is below it, 2 on a usage or config error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent
_ROOT = EXAMPLES.parent
for _p in (
    _ROOT / "packages" / "wincua" / "src",
    _ROOT / "packages" / "wincua-driver" / "src",
    _ROOT / "packages" / "wincua-providers" / "src",
    _ROOT / "packages" / "wincua-hitl" / "src",
):
    sys.path.insert(0, str(_p))

DEMOS = ("browser", "calculator")
# Top-level windows a previous run may leave open (form title changes to "wincua form demo — OK: ...").
WINDOW_TITLES = {"browser": ("wincua form demo",), "calculator": ("Calculator",)}

_PASS_MARKERS = {
    "browser_form_demo.py": re.compile(r"^golden '.*': PASS", re.M),
    "calculator_llm_demo.py": re.compile(r"^verify: .*-> PASS", re.M),
    "calculator_arith_demo.py": re.compile(r"^result\.ok=True", re.M),
}
_PATH_RE = re.compile(r"""(recordings[\\/][^\s'"),]+)""")
_FATAL_PREFIXES = (
    "HOST-WINDOWS-ONLY",
    "CONFIG ERROR",
    "config error",
    "BROWSER NOT FOUND",
    "Missing fixture",
    "open_url failed",
)


@dataclass
class RunResult:
    demo: str
    run: int
    status: str
    duration_s: float
    exit_code: int | None
    reason: str = ""
    command: list[str] = field(default_factory=list)
    log: str = ""
    recordings: list[dict] = field(default_factory=list)
    fallback: str = ""
    human_turns: int = 0

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


def demo_command(demo: str, provider: str, args: argparse.Namespace, python: str = sys.executable) -> list[str]:
    if demo == "browser":
        cmd = [python, str(EXAMPLES / "browser_form_demo.py"), "--provider", provider, "--browser", args.browser]
        model = provider != "scripted"
    elif provider == "scripted":
        cmd = [python, str(EXAMPLES / "calculator_arith_demo.py")]
        model = False
    else:
        cmd = [python, str(EXAMPLES / "calculator_llm_demo.py"), "--provider", provider]
        model = True
    if args.mock:
        cmd.append("--mock")
    if args.config and model:
        cmd += ["--config", args.config]
    if args.timeout is not None:
        cmd += ["--timeout", str(args.timeout)]
    if model and args.max_turns is not None:
        cmd += ["--max-turns", str(args.max_turns)]
    return cmd


def run_process(cmd: list[str], *, timeout_s: float, echo: bool = False, allow_human: bool = False) -> tuple[int | None, str, bool]:
    """Run ``cmd`` with stdout+stderr merged; returns (exit code, output, timed out)."""
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdin=None if allow_human else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=os.getcwd(),
    )
    lines: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace")
            lines.append(line)
            if echo:
                sys.stdout.write("    | " + line)
                sys.stdout.flush()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()
    reader.join(timeout=5)
    return (None if timed_out else proc.returncode), "".join(lines), timed_out


def failure_reason(output: str, exit_code: int | None, *, timed_out: bool, timeout_s: float, pass_seen: bool) -> str:
    """One line saying why a run failed, most specific first."""
    if timed_out:
        last = [ln.strip() for ln in output.splitlines() if ln.strip()][-1:] or ["(no output)"]
        return f"timed out after {timeout_s:.0f}s; last line: {last[0][:160]}"
    lines = [ln.rstrip() for ln in output.splitlines()]
    for ln in lines:
        s = ln.strip()
        if s.startswith(_FATAL_PREFIXES):
            return s[:240]
    parts: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("golden ") and s.endswith(")") and ": FAIL" in s:
            parts.append(s)
            break
        if s.startswith("verify:") and "FAIL" in s:
            parts.append(s)
            break
    for ln in lines:
        m = re.match(r"\s*result\.ok=False message=(.*)$", ln)
        if m:
            parts.append(f"agent: {m.group(1)}")
            break
    for ln in lines:
        s = ln.strip()
        if s.startswith("ESCALATE:") or "FAILED:" in s:
            parts.append(s)
            break
    if not parts and pass_seen and exit_code == 0:
        return ""
    if not parts:
        tail = [ln.strip() for ln in lines if ln.strip()][-1:] or ["(no output)"]
        parts.append(f"exit code {exit_code}: {tail[0]}")
    return " | ".join(parts)[:400]


def fallback_note(output: str) -> str:
    """The line saying the requested provider was skipped, with its reason."""
    lines = [ln.strip() for ln in output.splitlines()]
    for s in lines:
        if "falling back to" in s:
            return s[:240]
    for s in lines:
        if s.startswith("[unavailable]") and not s.startswith("[unavailable] null"):
            return s[:240]
    return ""


def recording_paths(output: str, cwd: Path) -> list[dict]:
    seen: list[str] = []
    for m in _PATH_RE.finditer(output):
        p = m.group(1).rstrip(".:;")
        if p not in seen:
            seen.append(p)
    return [{"path": p, "exists": (cwd / p).exists()} for p in seen]


def score_run(demo: str, run: int, cmd: list[str], exit_code: int | None, output: str, timed_out: bool,
              duration_s: float, timeout_s: float, log: str, cwd: Path) -> RunResult:
    script = Path(cmd[1]).name
    pass_seen = bool(_PASS_MARKERS[script].search(output))
    human = len(re.findall(r"^\s*HUMAN: ", output, re.M))
    ok = exit_code == 0 and pass_seen and not timed_out
    reason = "" if ok else failure_reason(output, exit_code, timed_out=timed_out, timeout_s=timeout_s, pass_seen=pass_seen)
    if ok and human > 1:
        ok, reason = False, f"needed {human} human clarifications (the gate allows 1 per run)"
    if not ok and not reason:
        reason = f"exit code {exit_code} but no PASS line from {script}"
    note = fallback_note(output)
    if not ok and note and note not in reason:
        reason = f"{note} | {reason}"[:400]
    return RunResult(
        demo=demo,
        run=run,
        status="PASS" if ok else "FAIL",
        duration_s=round(duration_s, 2),
        exit_code=exit_code,
        reason=reason,
        command=cmd,
        log=log,
        recordings=recording_paths(output, cwd),
        fallback=note,
        human_turns=human,
    )


def close_demo_windows(titles: tuple[str, ...]) -> int:
    """Post WM_CLOSE to visible top-level windows whose title starts with one of ``titles`` (Windows only)."""
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if any(title == t or title.startswith(t + " ") for t in titles):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    for hwnd in found:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    if found:
        time.sleep(1.5)
    return len(found)


def summarize(results: list[RunResult], demos: list[str], gate: float) -> dict:
    out = {}
    for demo in demos:
        rs = [r for r in results if r.demo == demo]
        passes = sum(r.passed for r in rs)
        rate = passes / len(rs) if rs else 0.0
        durations = [r.duration_s for r in rs]
        out[demo] = {
            "runs": len(rs),
            "passes": passes,
            "pass_rate": round(rate, 4),
            "meets_gate": bool(rs) and rate >= gate - 1e-9,
            "median_duration_s": round(statistics.median(durations), 2) if durations else None,
        }
    return out


def resolve_provider(args: argparse.Namespace) -> tuple[str, str]:
    """(provider name, where it came from). Raises ``ConfigError``."""
    if args.provider:
        return args.provider, "--provider"
    from wincua.config import load_config

    cfg = load_config(args.config)
    where = f"[provider].default in {cfg.path}" if cfg.path else "built-in default (no wincua.toml found)"
    return cfg.provider.default, where


def render_table(results: list[RunResult]) -> str:
    rows = [("demo", "run", "result", "secs", "exit", "reason")]
    for r in results:
        rows.append((r.demo, str(r.run), r.status, f"{r.duration_s:.1f}", "-" if r.exit_code is None else str(r.exit_code),
                     r.reason[:90] if r.reason else ""))
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    lines = []
    for i, row in enumerate(rows):
        lines.append("  ".join(c.ljust(w) for c, w in zip(row[:5], widths)) + "  " + row[5])
        if i == 0:
            lines.append("  ".join("-" * w for w in widths) + "  " + "-" * 6)
    return "\n".join(lines)


def render_gate(summary: dict, gate: float) -> str:
    lines = []
    for demo, s in summary.items():
        verdict = "MEETS GATE" if s["meets_gate"] else "BELOW GATE"
        lines.append(
            f"{demo:<10} {s['passes']}/{s['runs']} passed = {s['pass_rate']:.0%}  (gate {gate:.0%})  {verdict}"
            f"  median {s['median_duration_s']}s"
        )
    return "\n".join(lines)


def write_markdown(path: Path, report: dict, results: list[RunResult]) -> None:
    gate = report["gate"]
    lines = [
        "# wincua reliability report",
        "",
        f"- Started: {report['started']}  ",
        f"- Provider: `{report['provider']}` ({report['provider_source']})  ",
        f"- Backend: {'mock' if report['mock'] else 'windows'}  ",
        f"- Python: {report['python']} on {report['platform']}  ",
        f"- Gate: pass rate >= {gate:.0%} per demo. Overall: **{'MEETS GATE' if report['meets_gate'] else 'BELOW GATE'}**",
        "",
        "| Demo | Passed | Pass rate | Gate | Median secs |",
        "| --- | --- | --- | --- | --- |",
    ]
    for demo, s in report["summary"].items():
        lines.append(
            f"| {demo} | {s['passes']}/{s['runs']} | {s['pass_rate']:.0%} | "
            f"{'meets' if s['meets_gate'] else '**below**'} | {s['median_duration_s']} |"
        )
    lines += ["", "| Demo | Run | Result | Secs | Exit | Reason | Log | Recordings |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        recs = "<br>".join(f"`{x['path']}`" + ("" if x["exists"] else " (not written)") for x in r.recordings)
        reason = r.reason.replace("|", "\\|") or "-"
        exit_code = "timeout" if r.exit_code is None else r.exit_code
        lines.append(f"| {r.demo} | {r.run} | {r.status} | {r.duration_s:.1f} | {exit_code} | {reason} | `{r.log}` | {recs or '-'} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", default=None, help="ollama | llamacpp | anthropic | openai | null | scripted | <custom>; default [provider].default from wincua.toml")
    p.add_argument("--config", default=None, help="Path to wincua.toml (default lookup as the demos)")
    p.add_argument("--runs", type=int, default=5, help="Runs per demo (default 5; the section 8 metric uses 20)")
    p.add_argument("--demos", nargs="+", choices=DEMOS, default=list(DEMOS), help="Which demos (default both)")
    p.add_argument("--gate", type=float, default=0.8, help="Required pass rate per demo (default 0.8 = 4/5)")
    p.add_argument("--run-timeout", type=float, default=300, help="Kill a run after this many seconds (default 300)")
    p.add_argument("--timeout", type=float, default=None, help="Launch wait passed to the demos (--timeout)")
    p.add_argument("--max-turns", type=int, default=None, help="Model turn cap passed to the demos")
    p.add_argument("--browser", default="msedge", choices=("msedge", "chrome"))
    p.add_argument("--mock", action="store_true", help="Pass --mock to the demos (any OS, fake desktop)")
    p.add_argument("--out", default=None, help="Report folder (default recordings/reliability-<time>)")
    p.add_argument("--interleave", action="store_true", help="Alternate demos run by run instead of all browser runs first")
    p.add_argument("--keep-windows", action="store_true", help="Do not close demo windows left by the previous run")
    p.add_argument("--allow-human", action="store_true", help="Give the demos this terminal's stdin so a fallback can ask you")
    p.add_argument("-v", "--verbose", action="store_true", help="Echo each demo's output as it runs")
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs must be at least 1")
    if not 0 < args.gate <= 1:
        p.error("--gate must be in (0, 1]")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True, errors="replace")
    except (AttributeError, ValueError):
        pass
    from wincua.config import ConfigError

    try:
        provider, source = resolve_provider(args)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    if not args.mock and sys.platform != "win32":
        print(f"HOST-WINDOWS-ONLY: the demos drive the Win11 desktop (or pass --mock). platform={sys.platform!r}", file=sys.stderr)
        return 2

    cwd = Path.cwd()
    started = time.strftime("%Y-%m-%d %H:%M:%S %z")
    out_dir = Path(args.out) if args.out else Path("recordings") / f"reliability-{time.strftime('%Y%m%d-%H%M%S')}"
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)

    print("wincua reliability")
    print(f"  provider: {provider} ({source})")
    if provider == "null" and not args.allow_human:
        print("  note: provider 'null' asks the human every step; unattended runs will fail (pass --allow-human)")
    print(f"  demos:    {', '.join(args.demos)} x {args.runs}  gate {args.gate:.0%}  backend {'mock' if args.mock else 'windows'}")
    print(f"  report:   {out_dir}")

    order = [(d, k) for k in range(1, args.runs + 1) for d in args.demos] if args.interleave else \
        [(d, k) for d in args.demos for k in range(1, args.runs + 1)]
    results: list[RunResult] = []
    for demo, k in order:
        if not args.mock and not args.keep_windows:
            closed = close_demo_windows(WINDOW_TITLES[demo])
            if closed:
                print(f"  closed {closed} leftover {demo} window(s)")
        cmd = demo_command(demo, provider, args)
        print(f"[{demo} {k}/{args.runs}] {' '.join(Path(c).name if i < 2 else c for i, c in enumerate(cmd))}")
        t0 = time.monotonic()
        code, output, timed_out = run_process(cmd, timeout_s=args.run_timeout, echo=args.verbose, allow_human=args.allow_human)
        duration = time.monotonic() - t0
        log = out_dir / "logs" / f"{demo}-{k:02d}.log"
        log.write_text(f"$ {subprocess.list2cmdline(cmd)}\n{output}", encoding="utf-8")
        r = score_run(demo, k, cmd, code, output, timed_out, duration, args.run_timeout, str(log), cwd)
        results.append(r)
        print(f"[{demo} {k}/{args.runs}] {r.status} in {r.duration_s:.1f}s" + (f": {r.reason}" if r.reason else ""))
    if not args.mock and not args.keep_windows:
        for demo in args.demos:
            close_demo_windows(WINDOW_TITLES[demo])

    summary = summarize(results, args.demos, args.gate)
    meets = all(s["meets_gate"] for s in summary.values())
    report = {
        "started": started,
        "finished": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "provider": provider,
        "provider_source": source,
        "config": args.config,
        "mock": args.mock,
        "runs_per_demo": args.runs,
        "gate": args.gate,
        "meets_gate": meets,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "summary": summary,
        "runs": [asdict(r) for r in results],
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(out_dir / "report.md", report, results)

    print()
    print(render_table(results))
    print()
    print(render_gate(summary, args.gate))
    print(f"report: {out_dir / 'report.md'}  {out_dir / 'report.json'}")
    print(f"gate {args.gate:.0%}: {'PASS' if meets else 'FAIL'}")
    return 0 if meets else 1


if __name__ == "__main__":
    raise SystemExit(main())
