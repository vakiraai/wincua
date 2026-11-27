#!/usr/bin/env python3
"""Browser form demo — MVP co-hero.

**Host Windows only.** Requires interactive desktop + Edge/Chrome + allow-list.

Intended flow (Phase 1):
1. Start Agent with OllamaProvider (or NullProvider → HITL).
2. Focus/open a local file:// form page in Edge.
3. observe() → fill fields via UIA element_id clicks/types → submit.
4. Assert expected result string; log model vs human steps.

This stub documents the API; it does not drive the desktop yet.
"""

from __future__ import annotations

import asyncio
import platform
import sys


def _require_windows() -> None:
    if platform.system() != "Windows":
        print(
            "HOST-WINDOWS-ONLY: browser_form_demo requires an interactive "
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
            "Open the local form demo page in Edge, fill name/email, submit, "
            "and leave the success message visible."
        ),
        provider=NullProvider(),  # swap for OllamaProvider(model="qwen2.5-vl:7b")
        hitl=LocalUI(port=8765),
        allow=["msedge.exe", "chrome.exe"],
        mode="foreground",
        recorder=True,
    )

    print("browser_form_demo stub — intended Agent config:")
    print(f"  task: {agent.task!r}")
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
