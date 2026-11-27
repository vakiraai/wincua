#!/usr/bin/env python3
"""Browser form demo — MVP co-hero.

**Host Windows only** for a real desktop run (Edge or Chrome). On non-Windows
pass ``--mock`` to exercise the scripted Agent loop.

Flow:
1. Open ``examples/fixtures/form.html`` via file:// in Edge (Chrome fallback).
2. Fill name/email and submit via UIA when exposed; otherwise keyboard Tab/type.
3. Assert the success string appears (UIA name/title or page status).

## Limits (honest)

Chromium (Edge/Chrome) accessibility trees for ``file://`` pages are **less
stable** than Calculator's native UIA. Common failure modes:

- Inputs may lack durable AutomationIds; we prefer ``aria-label`` / Name.
- Focus can land in the address bar / sidebar — script sends Tab heuristics.
- Headless / remote desktop / non-interactive sessions will fail.
- Cross-origin / restricted file:// policies are out of scope; use the bundled fixture.

If UIA fill is unreliable on your build, the keyboard path (Tab → type → Tab →
type → Enter) is the supported fallback and is what this scripted demo uses
after focusing the browser window.
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

    # Keyboard-first path: reliable across Edge/Chrome a11y variance.
    return [
        PlanStep(
            kind="tool",
            name="open_url",
            args={
                "url": fixture_uri,
                "browser": "msedge",
                "target": "wincua form demo",
                "timeout_s": 45,
            },
        ),
        PlanStep(kind="tool", name="wait", args={"seconds": 1.0}),
        PlanStep(
            kind="tool",
            name="focus",
            args={"title": "wincua form demo", "timeout_s": 20},
        ),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.4}),
        # Move focus into the page document, then to the first field.
        PlanStep(kind="tool", name="key", args={"keys": "F6"}),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.2}),
        PlanStep(kind="tool", name="key", args={"keys": "tab"}),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.15}),
        PlanStep(kind="tool", name="type", args={"text": NAME_VALUE}),
        PlanStep(kind="tool", name="key", args={"keys": "tab"}),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.15}),
        PlanStep(kind="tool", name="type", args={"text": EMAIL_VALUE}),
        PlanStep(kind="tool", name="key", args={"keys": "tab"}),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.15}),
        PlanStep(kind="tool", name="key", args={"keys": "enter"}),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.6}),
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
    print("  note: Chromium UIA is best-effort; keyboard Tab/type is the reliable path.")

    result = await agent.run()
    print(f"result.ok={result.ok} message={result.message!r}")
    for step in result.steps:
        if step.get("kind") in {"tool", "error", "done", "abort"}:
            print(f"  - {step}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
