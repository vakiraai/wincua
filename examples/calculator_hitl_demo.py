#!/usr/bin/env python3
"""Calculator human-in-the-loop demo — no model required.

The agent runs with ``NullProvider``, so every step is planned by you in the
local HITL UI (``http://127.0.0.1:8765/?token=...``, opened automatically):
pick a Calculator button, type a command such as ``invoke auto:num1Button``,
take over the desktop, or mark the task done. Goal: compute 17 + 25 = 42.

``--provider scripted`` instead runs the fixed 17 + 25 script with the ``=``
press flagged as risky, so you approve or reject it in the UI.

``--hitl cli`` asks in this terminal instead of the browser. ``--mock`` runs
against MockBackend on any OS (no desktop, no screenshot).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    _ROOT / "packages" / "wincua" / "src",
    _ROOT / "packages" / "wincua-driver" / "src",
    _ROOT / "packages" / "wincua-providers" / "src",
    _ROOT / "packages" / "wincua-hitl" / "src",
):
    sys.path.insert(0, str(_p))

GOLDEN = "42"
EXPRESSION = "17 + 25"
ALLOW = [
    "CalculatorApp.exe",
    "ApplicationFrameHost.exe",
    "calc.exe",
    "calculator",
    "calculator:",
    "explorer.exe",
]


def scripted_steps():
    from wincua_providers.base import PlanStep

    steps = []
    for auto_id in ("clearButton", "num1Button", "num7Button", "plusButton", "num2Button", "num5Button"):
        steps.append(PlanStep(kind="tool", name="invoke", args={"auto_id": auto_id, "target": "Calculator"}))
        steps.append(PlanStep(kind="tool", name="wait", args={"seconds": 0.15}))
    steps.append(
        PlanStep(
            kind="tool",
            name="invoke",
            args={"auto_id": "equalButton", "target": "Calculator", "risk": "confirm"},
        )
    )
    steps.append(
        PlanStep(
            kind="tool",
            name="assert_display",
            args={"target": "Calculator", "equals": GOLDEN, "auto_id": "CalculatorResults"},
        )
    )
    steps.append(PlanStep(kind="done", message=f"{EXPRESSION} == {GOLDEN}"))
    return steps


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mock", action="store_true", help="MockBackend; any OS, no desktop")
    parser.add_argument("--hitl", choices=["auto", "ui", "cli"], default="auto", help="auto = [hitl] mode from wincua.toml (default ui)")
    parser.add_argument("--provider", choices=["null", "scripted"], default="null")
    parser.add_argument("--config", default=None, help="Path to wincua.toml")
    parser.add_argument("--port", type=int, default=None, help="HITL UI port (default from config, 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Print the UI URL instead of opening it")
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--no-launch", action="store_true", help="Skip launch/focus (open Calculator yourself first)")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    print("calculator_hitl_demo")
    print(f"  python:   {sys.version.split()[0]} ({sys.executable})")

    from wincua import Agent
    from wincua_driver import Semantic, act, is_windows, observe
    from wincua_driver.backend import set_backend
    from wincua_hitl import from_settings, load_hitl_settings
    from wincua_providers import NullProvider, ScriptedProvider

    if args.mock:
        set_backend("mock")
        backend = "mock"
    elif not is_windows():
        print(
            "HOST-WINDOWS-ONLY: calculator_hitl_demo drives the real Calculator (or pass --mock). "
            f"platform={sys.platform!r}",
            file=sys.stderr,
        )
        return 1
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _windows_preflight import require_windows_uia_stack  # noqa: E402

        if require_windows_uia_stack() != 0:
            return 1
        backend = "windows"
        set_backend("windows", allow=ALLOW)

    try:
        settings = load_hitl_settings(args.config)
        overrides = {"port": args.port, "open_browser": False if args.no_browser else None}
        if args.hitl != "auto":
            overrides["mode"] = args.hitl
        settings = settings.with_overrides(**overrides)
    except (ValueError, FileNotFoundError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if settings.mode == "off":
        print("[hitl] mode is 'off'; this demo needs a human. Use --hitl ui or --hitl cli.", file=sys.stderr)
        return 2
    hitl = from_settings(settings)
    print(f"  backend:  {backend}")
    print(f"  hitl:     {settings.mode}" + (f" (config {settings.source})" if settings.source else " (no config file)"))

    for name, sargs in (
        ("launch", {"app": "calculator", "title": "Calculator", "timeout_s": 45}),
        ("focus", {"title": "Calculator", "timeout_s": 15}),
    ):
        if args.no_launch:
            break
        print(f"  {name} Calculator ... (can take up to {sargs['timeout_s']}s)")
        t0 = time.monotonic()
        res = await act(Semantic(name=name, args=sargs))
        print(f"  {name}: {'ok' if res.ok else 'FAILED'} in {time.monotonic() - t0:.1f}s")
        if not res.ok:
            print(f"{name} failed: {res.error.message if res.error else res}", file=sys.stderr)
            return 1

    provider = NullProvider() if args.provider == "null" else ScriptedProvider(scripted_steps())
    agent = Agent(
        task=f"In Calculator, compute {EXPRESSION} and leave {GOLDEN} on the display.",
        provider=provider,
        hitl=hitl,
        allow=ALLOW,
        mode="foreground",
        recorder=True,
        backend=backend,
        observe_target="Calculator",
        max_turns=args.max_steps,
    )

    print(f"  provider: {agent.provider.name}")
    if args.provider == "null":
        print("  You plan every step. Try: Clear, One, Seven, Plus, Two, Five, Equals, then Mark done.")
    print("  Reading the Calculator UI tree for the first step (a few seconds on Windows) ...")

    result = await agent.run()

    obs = await observe(target="Calculator", include=["uia", "app"])
    display = str((obs.app or {}).get("display") or "")
    human = sum(1 for s in result.steps if s.get("by") == "human")
    print(f"result.ok={result.ok} message={result.message!r}")
    print(f"  steps by human: {human}; confirmations: {sum(1 for s in result.steps if s.get('request') == 'confirm')}")
    print(f"  display: {display!r}")
    golden_ok = display.rstrip().endswith(GOLDEN)
    print(f"  golden {GOLDEN}: {'PASS' if golden_ok else 'not reached'}")
    return 0 if result.ok and golden_ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
