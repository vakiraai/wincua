#!/usr/bin/env python3
"""Calculator arithmetic demo — MVP co-hero.

**Host Windows only** for a real desktop run. Requires Calculator on an
interactive session. On non-Windows this script exits with a clear error
(use ``--mock`` to exercise the scripted Agent loop via MockBackend).

Flow:
1. Launch/focus Calculator.
2. Clear, enter 17 + 25, press = via UIA Invoke (AutomationIds).
3. Read display via UIA (``CalculatorResults``); assert golden 42.

By default the steps are scripted (no LLM). ``--provider NAME`` / ``--config
PATH`` plan with a real model from ``wincua.toml`` instead (same flow as
``calculator_llm_demo.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from a source checkout without install
_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    _ROOT / "packages" / "wincua" / "src",
    _ROOT / "packages" / "wincua-driver" / "src",
    _ROOT / "packages" / "wincua-providers" / "src",
    _ROOT / "packages" / "wincua-hitl" / "src",
):
    sys.path.insert(0, str(_p))

GOLDEN = 42
EXPRESSION = "17 + 25"

# Win10/11 Store Calculator AutomationIds
_CALC_BUTTONS = (
    "clearButton",
    "num1Button",
    "num7Button",
    "plusButton",
    "num2Button",
    "num5Button",
    "equalButton",
)


def build_calculator_steps():
    from wincua_providers.base import PlanStep

    steps: list[PlanStep] = [
        PlanStep(
            kind="tool",
            name="launch",
            args={
                # Uses calculator: protocol / AppsFolder — not Application.start(calc.exe)
                "app": "calculator",
                "title": "Calculator",
                "timeout_s": 45,
            },
        ),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.5}),
        PlanStep(
            kind="tool",
            name="focus",
            args={"title": "Calculator", "timeout_s": 15},
        ),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.3}),
    ]
    for auto_id in _CALC_BUTTONS:
        steps.append(
            PlanStep(
                kind="tool",
                name="invoke",
                args={"auto_id": auto_id, "target": "Calculator"},
            )
        )
        steps.append(PlanStep(kind="tool", name="wait", args={"seconds": 0.15}))
    steps.append(
        PlanStep(
            kind="tool",
            name="assert_display",
            args={"target": "Calculator", "equals": str(GOLDEN), "auto_id": "CalculatorResults"},
        )
    )
    steps.append(PlanStep(kind="done", message=f"{EXPRESSION} == {GOLDEN}"))
    return steps


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run scripted loop against MockBackend (no desktop; any OS)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Override launch wait seconds (also: env WINCUA_LAUNCH_TIMEOUT_S)",
    )
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _llm_common import add_provider_args, load_demo_config, wants_model  # noqa: E402

    add_provider_args(parser)
    args = parser.parse_args(argv)
    cfg = None
    if wants_model(args):
        cfg = load_demo_config(args)
        if cfg is None:
            return 1

    from wincua import Agent
    from wincua_driver import is_windows
    from wincua_driver.backend import set_backend
    from wincua_providers import ScriptedProvider

    # Local helper next to demos (same checkout)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _windows_preflight import require_windows_uia_stack  # noqa: E402

    if args.mock:
        set_backend("mock")
        backend = "mock"
    elif not is_windows():
        print(
            "HOST-WINDOWS-ONLY: calculator_arith_demo needs Win11 (or pass --mock). "
            f"platform={sys.platform!r}",
            file=sys.stderr,
        )
        return 1
    else:
        if require_windows_uia_stack() != 0:
            return 1
        backend = "windows"
        if args.timeout is not None:
            import os

            os.environ["WINCUA_LAUNCH_TIMEOUT_S"] = str(args.timeout)

    if cfg is not None:
        from calculator_llm_demo import run_with_model

        args.max_turns = 25
        print("calculator_arith_demo (model-planned)")
        return await run_with_model(args, backend, cfg)

    steps = build_calculator_steps()
    if args.timeout is not None:
        steps[0].args["timeout_s"] = args.timeout

    agent = Agent(
        task=(
            f"Open Calculator and compute {EXPRESSION}; "
            f"leave the result visible (golden={GOLDEN})."
        ),
        provider=ScriptedProvider(steps),
        hitl=None,  # scripted — no LLM / HITL required
        allow=[
            "CalculatorApp.exe",
            "ApplicationFrameHost.exe",
            "calc.exe",
            "calculator",
            "calculator:",
            "explorer.exe",  # shell:AppsFolder launch helper
        ],
        mode="foreground",
        recorder=True,
        backend=backend,
        observe_target="Calculator",
    )

    print("calculator_arith_demo")
    print(f"  task: {agent.task!r}")
    print(f"  golden: {GOLDEN}")
    print(f"  backend: {backend}")
    print(f"  allow: {list(agent.allow)}")
    print(f"  provider: {agent.provider.name}")

    result = await agent.run()
    print(f"result.ok={result.ok} message={result.message!r}")
    for step in result.steps:
        if step.get("kind") in {"tool", "error", "done", "abort"}:
            print(f"  - {step}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
