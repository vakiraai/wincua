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

from wincua_driver.window_state import WINDOW_STATE_CODES  # noqa: E402

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


def build_calculator_steps(*, pause_s: float = 0.0, click: bool = False):
    """Scripted steps. ``pause_s`` waits after focus (cover or minimize Calculator
    then); ``click`` presses buttons with real mouse clicks instead of UIA Invoke."""
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
    if pause_s > 0:
        steps.append(PlanStep(kind="tool", name="wait", args={"seconds": pause_s}))
    for auto_id in _CALC_BUTTONS:
        if click:
            step = PlanStep(kind="tool", name="click", args={"element_id": f"auto:{auto_id}"})
        else:
            step = PlanStep(kind="tool", name="invoke", args={"auto_id": auto_id, "target": "Calculator"})
        steps.append(step)
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
    parser.add_argument(
        "--pause",
        type=float,
        default=0.0,
        metavar="S",
        help="Wait S seconds after Calculator is focused, e.g. to cover it with Notepad or minimize it",
    )
    parser.add_argument(
        "--click",
        action="store_true",
        help="Press buttons with mouse clicks instead of UIA Invoke (exercises the target_obscured check)",
    )
    parser.add_argument(
        "--no-auto-focus",
        action="store_true",
        help="Fail with window_minimized / target_obscured / … instead of restoring and focusing once "
        "(sets WINCUA_AUTO_FOCUS=0)",
    )
    parser.add_argument(
        "--simulate",
        default=None,
        choices=sorted(WINDOW_STATE_CODES),
        help="With --mock: make the fake Calculator report this window state (focus cannot fix it)",
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

    if args.no_auto_focus:
        import os

        os.environ["WINCUA_AUTO_FOCUS"] = "0"

    if args.mock:
        mock = set_backend("mock")
        backend = "mock"
        if args.simulate:
            mock.window_issue, mock.window_issue_sticky = args.simulate, True
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

    steps = build_calculator_steps(pause_s=args.pause, click=args.click)
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
    print(f"  buttons: {'mouse click' if args.click else 'UIA Invoke'}; auto-focus: {not args.no_auto_focus}")
    if args.pause:
        print(f"  pausing {args.pause:g}s after Calculator is focused: cover it with another window or minimize it now")

    result = await agent.run()
    print(f"result.ok={result.ok} message={result.message!r}")
    for step in result.steps:
        if step.get("kind") in {"tool", "error", "done", "abort"}:
            print(f"  - {step}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
