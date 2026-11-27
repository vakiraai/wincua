#!/usr/bin/env python3
"""Calculator arithmetic demo — MVP co-hero.

**Host Windows only.** Requires CalculatorApp.exe on an interactive desktop.

Intended flow (Phase 1):
1. Launch/focus Calculator.
2. Clear, enter 17 + 25, press = via UIA Invoke/click.
3. Read display (UIA preferred; screenshot+OCR fallback).
4. Compare to golden 42; HITL if mode ambiguous.

This stub documents the API; it does not drive the desktop yet.
"""

from __future__ import annotations

import asyncio
import platform
import sys

GOLDEN = 42
EXPRESSION = "17 + 25"


def _require_windows() -> None:
    if platform.system() != "Windows":
        print(
            "HOST-WINDOWS-ONLY: calculator_arith_demo requires an interactive "
            "Windows desktop session. Exiting without desktop I/O.",
            file=sys.stderr,
        )


async def main() -> int:
    _require_windows()

    from wincua import Agent
    from wincua.hitl import LocalUI
    from wincua.providers import NullProvider

    agent = Agent(
        task=(
            f"Open Calculator and compute {EXPRESSION}; "
            f"leave the result visible (golden={GOLDEN})."
        ),
        provider=NullProvider(),  # swap for OllamaProvider(model="qwen2.5-vl:7b")
        hitl=LocalUI(port=8765),
        allow=["CalculatorApp.exe", "ApplicationFrameHost.exe"],
        mode="foreground",
        recorder=True,
    )

    print("calculator_arith_demo stub — intended Agent config:")
    print(f"  task: {agent.task!r}")
    print(f"  golden: {GOLDEN}")
    print(f"  allow: {list(agent.allow)}")
    print(f"  provider: {agent.provider.name}")
    print(f"  hitl: {agent.hitl.url}")

    if platform.system() != "Windows":
        return 0

    result = await agent.run()
    print(f"result.ok={result.ok} message={result.message!r}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
