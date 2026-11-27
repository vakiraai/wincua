#!/usr/bin/env python3
"""Browser form demo — MVP co-hero.

**Host Windows only** for a real desktop run (Edge or Chrome). On non-Windows
pass ``--mock`` to exercise the scripted Agent loop.

Flow:
1. Open ``examples/fixtures/form.html`` in a **new** Edge/Chrome window with a
   throwaway profile (avoids reused tabs + first-run infobars stealing focus).
2. Fill name/email via UIA ValuePattern on the "Full name" / "Email" edits
   (verified), then Invoke "Submit form".
3. Assert the success string appears.

## Limits (honest)

Chromium accessibility for ``file://`` pages is less stable than Calculator UIA.
Keyboard Tab/type remains a fallback inside the driver when ValuePattern fails.
Headless / remote / non-interactive sessions will fail.
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

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "form.html"
EXPECTED_CONTAINS = "OK: Ada Lovelace"
NAME_VALUE = "Ada Lovelace"
EMAIL_VALUE = "ada@example.com"


def build_browser_steps(fixture_uri: str):
    from wincua_providers.base import PlanStep

    # UIA-first path: set Edit values + Invoke submit (keyboard is driver fallback).
    return [
        PlanStep(
            kind="tool",
            name="open_url",
            args={
                "url": fixture_uri,
                "browser": "msedge",
                "target": "wincua form demo",
                "timeout_s": 45,
                "new_window": True,
                "isolated_profile": True,
            },
        ),
        PlanStep(kind="tool", name="wait", args={"seconds": 1.5}),
        PlanStep(
            kind="tool",
            name="focus",
            args={"title": "wincua form demo", "timeout_s": 20},
        ),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.4}),
        PlanStep(
            kind="tool",
            name="type",
            args={
                "name": "Full name",
                "text": NAME_VALUE,
                "target": "wincua form demo",
            },
        ),
        PlanStep(
            kind="tool",
            name="type",
            args={
                "name": "Email",
                "text": EMAIL_VALUE,
                "target": "wincua form demo",
            },
        ),
        PlanStep(
            kind="tool",
            name="invoke",
            args={"name": "Submit form", "target": "wincua form demo"},
        ),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.8}),
        PlanStep(
            kind="tool",
            name="assert_text",
            args={
                "target": "wincua form demo",
                "contains": EXPECTED_CONTAINS,
            },
        ),
        PlanStep(kind="done", message="form submitted"),
    ]


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run scripted loop against MockBackend (no desktop; any OS)",
    )
    parser.add_argument(
        "--browser",
        default="msedge",
        choices=("msedge", "chrome"),
        help="Browser executable preference on Windows (default: msedge)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Override launch wait seconds (also: env WINCUA_LAUNCH_TIMEOUT_S)",
    )
    args = parser.parse_args(argv)

    from wincua import Agent
    from wincua_driver import is_windows
    from wincua_driver.backend import set_backend
    from wincua_driver.backend.windows import resolve_browser_executable
    from wincua_providers import ScriptedProvider

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _windows_preflight import require_windows_uia_stack  # noqa: E402

    if not FIXTURE.is_file():
        print(f"Missing fixture: {FIXTURE}", file=sys.stderr)
        print("Run from the repo checkout so examples/fixtures/form.html exists.", file=sys.stderr)
        return 1

    fixture_path = FIXTURE.resolve()
    fixture_uri = fixture_path.as_uri()

    if args.mock:
        set_backend("mock")
        backend = "mock"
        resolved_browser = None
    elif not is_windows():
        print(
            "HOST-WINDOWS-ONLY: browser_form_demo needs Win11 (or pass --mock). "
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
        resolved_browser = resolve_browser_executable(args.browser)
        if resolved_browser is None:
            alt = "chrome" if args.browser == "msedge" else "msedge"
            resolved_browser = resolve_browser_executable(alt)
        if resolved_browser is None:
            print(
                "BROWSER NOT FOUND: install Microsoft Edge or Google Chrome, "
                "or ensure msedge.exe / chrome.exe is under Program Files. "
                f"Preference was --browser {args.browser}.",
                file=sys.stderr,
            )
            return 1

    steps = build_browser_steps(fixture_uri)
    if not args.mock:
        steps[0].args["browser"] = args.browser
        if args.timeout is not None:
            steps[0].args["timeout_s"] = args.timeout

    agent = Agent(
        task=(
            "Open the local form demo page in Edge/Chrome, fill name/email, "
            "submit, and leave the success message visible."
        ),
        provider=ScriptedProvider(steps),
        hitl=None,
        allow=["msedge.exe", "chrome.exe"],
        mode="foreground",
        recorder=True,
        backend=backend,
        observe_target="wincua form demo",
    )

    print("browser_form_demo")
    print(f"  fixture: {fixture_path}")
    print(f"  fixture_uri: {fixture_uri}")
    print(f"  backend: {backend}")
    if resolved_browser is not None:
        print(f"  browser_exe: {resolved_browser}")
    print(f"  allow: {list(agent.allow)}")
    print(f"  provider: {agent.provider.name}")
    print("  note: opens --new-window + throwaway profile; fills via UIA ValuePattern.")

    result = await agent.run()
    print(f"result.ok={result.ok} message={result.message!r}")
    for step in result.steps:
        if step.get("kind") in {"tool", "error", "done", "abort"}:
            print(f"  - {step}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
