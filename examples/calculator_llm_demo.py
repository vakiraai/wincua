#!/usr/bin/env python3
"""Calculator demo planned by a real model (Ollama / llama.cpp / cloud).

The provider comes from ``wincua.toml`` (see ``wincua.example.toml``):
``--provider`` overrides ``[provider].default``; if it is unreachable the
config's ``fallback`` chain is tried, ending in ``null`` (ask the human).

    python examples/calculator_llm_demo.py --check                 # which provider would run?
    python examples/calculator_llm_demo.py --provider ollama       # Win11 desktop
    python examples/calculator_llm_demo.py --provider ollama --mock  # any OS, fake Calculator

After the model calls ``done`` the demo reads the display itself and checks 42.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    _ROOT / "packages" / "wincua" / "src",
    _ROOT / "packages" / "wincua-driver" / "src",
    _ROOT / "packages" / "wincua-providers" / "src",
    _ROOT / "packages" / "wincua-hitl" / "src",
):
    sys.path.insert(0, str(_p))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _llm_common import (  # noqa: E402
    add_provider_args,
    load_demo_config,
    preflight,
    print_run,
    verify_visible,
)

GOLDEN = "42"
EXPRESSION = "17 + 25"
BUTTONS = ("clearButton", "num1Button", "num7Button", "plusButton", "num2Button", "num5Button", "equalButton")
TASK = (
    f"Compute {EXPRESSION} in Windows Calculator in three steps.\n"
    "Step 1: launch(app='calculator', title='Calculator').\n"
    "Step 2, all in ONE reply: seven invoke calls, one per button, in this order: "
    + ", ".join(f"auto:{b}" for b in BUTTONS)
    + ". Press auto:clearButton only once, as the first of the seven.\n"
    f"Step 3: when the display shows {GOLDEN}, call done(message='{GOLDEN}')."
)
ALLOW = [
    "CalculatorApp.exe",
    "ApplicationFrameHost.exe",
    "calc.exe",
    "calculator",
    "calculator:",
    "explorer.exe",
]


async def run_with_model(args: argparse.Namespace, backend: str, cfg) -> int:
    from wincua import Agent

    await preflight(cfg, args.provider)
    agent = Agent(
        task=TASK,
        provider=args.provider,
        config=cfg,
        hitl=None,
        allow=ALLOW,
        mode="foreground",
        recorder=True,
        backend=backend,
        observe_target="Calculator",
        max_turns=args.max_turns,
    )
    print(f"  backend: {backend}")
    print(f"  provider: {getattr(agent.provider, 'describe', lambda: agent.provider_name)()}")
    result = await agent.run()
    print_run(agent, result)
    if result.recording_path:
        print(f"  recording: {result.recording_path}")
    if not result.ok:
        return 2
    ok, display = await verify_visible("Calculator", equals=GOLDEN)
    print(f"verify: display={display!r} expected {GOLDEN} -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 3


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_provider_args(parser)
    parser.add_argument("--mock", action="store_true", help="Fake Calculator (MockBackend; any OS)")
    parser.add_argument("--check", action="store_true", help="Probe the provider chain and exit")
    parser.add_argument("--max-turns", type=int, default=25)
    parser.add_argument("--timeout", type=float, default=None, help="Launch wait seconds")
    args = parser.parse_args(argv)

    print("calculator_llm_demo")
    cfg = load_demo_config(args)
    if cfg is None:
        return 1
    if args.check:
        await preflight(cfg, args.provider)
        return 0

    from wincua_driver import is_windows
    from wincua_driver.backend import set_backend

    if args.mock:
        set_backend("mock")
        backend = "mock"
    elif not is_windows():
        print(
            "HOST-WINDOWS-ONLY: needs Win11 (or pass --mock). " f"platform={sys.platform!r}",
            file=sys.stderr,
        )
        return 1
    else:
        from _windows_preflight import require_windows_uia_stack

        if require_windows_uia_stack() != 0:
            return 1
        backend = "windows"
        if args.timeout is not None:
            import os

            os.environ["WINCUA_LAUNCH_TIMEOUT_S"] = str(args.timeout)
    return await run_with_model(args, backend, cfg)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
