#!/usr/bin/env python3
"""Calculator arithmetic demo — MVP co-hero.

**Host Windows only** for a real desktop run. Requires Calculator on an
interactive session. On non-Windows this script exits with a clear error
(use ``--mock`` to exercise the scripted Agent loop via MockBackend).

Flow:
1. Launch/focus Calculator.
2. Clear, enter 17 + 25, press = via UIA Invoke (AutomationIds).
3. Read display via UIA (``CalculatorResults``); assert golden 42.
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
            args={"app": "calc.exe", "title": "Calculator"},
        ),
        PlanStep(kind="tool", name="wait", args={"seconds": 1.2}),
        PlanStep(
            kind="tool",
            name="focus",
            args={"title": "Calculator"},
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
    args = parser.parse_args(argv)

    from wincua import Agent
    from wincua_driver import is_windows
    from wincua_driver.backend import set_backend
    from wincua_providers import ScriptedProvider

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
        backend = "windows"

    agent = Agent(
        task=(
            f"Open Calculator and compute {EXPRESSION}; "
            f"leave the result visible (golden={GOLDEN})."
        ),
        provider=ScriptedProvider(build_calculator_steps()),
        hitl=None,  # scripted — no LLM / HITL required
        allow=["CalculatorApp.exe", "ApplicationFrameHost.exe", "calc.exe"],
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
