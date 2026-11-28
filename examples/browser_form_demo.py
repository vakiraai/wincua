#!/usr/bin/env python3
"""Browser form demo (MVP demo A): fill a local form in Edge, submit, check the result.

Opens ``examples/fixtures/form.html`` in a new Edge window (throwaway profile,
Chromium accessibility forced on), types into the "Full name" and "Email" Edit
controls (UIA ValuePattern, keyboard fallback), invokes "Submit form" and then
checks independently that the page shows ``OK: Ada Lovelace <ada@example.com>``.

    python examples/browser_form_demo.py                          # scripted, no model
    python examples/browser_form_demo.py --provider ollama        # planned by a model from wincua.toml
    python examples/browser_form_demo.py --provider null          # you plan each step in the HITL UI
    python examples/browser_form_demo.py --hitl ui                # scripted; approve the submit in the UI
    python examples/browser_form_demo.py --mock                   # any OS, fake form (MockBackend)
    python examples/browser_form_demo.py --dump-tree              # also print the Edge UIA tree

The last line is ``golden '<expected>': PASS`` or ``FAIL``. On a failed Windows
run the UIA tree (Edge frame, page Document, Edit values) is written to
``recordings/browser-form-<time>-uia.txt``; attach it when reporting.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _llm_common import add_provider_args, load_demo_config, print_run  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "form.html"
TARGET = "wincua form demo"
NAME_VALUE = "Ada Lovelace"
EMAIL_VALUE = "ada@example.com"
EXPECTED = f"OK: {NAME_VALUE} <{EMAIL_VALUE}>"
ALLOW = ["msedge.exe", "chrome.exe"]

# HTML id -> UIA AutomationId; <label for> -> Name. Both are passed so the
# driver can fall back to the Name if the AutomationId is missing.
FIELDS = (("fullName", "Full name", NAME_VALUE), ("email", "Email", EMAIL_VALUE))
SUBMIT = ("submitButton", "Submit form")


def open_args(fixture_uri: str, browser: str = "msedge", timeout_s: float = 45) -> dict:
    return {
        "url": fixture_uri,
        "browser": browser,
        "target": TARGET,
        "timeout_s": timeout_s,
        "new_window": True,
        "isolated_profile": True,
    }


def build_browser_steps(fixture_uri: str, *, browser: str = "msedge", timeout_s: float = 45, confirm_submit: bool = False):
    from wincua_providers.base import PlanStep

    steps = [
        PlanStep(kind="tool", name="open_url", args=open_args(fixture_uri, browser, timeout_s)),
        PlanStep(kind="tool", name="focus", args={"title": TARGET, "timeout_s": 20}),
    ]
    for auto_id, label, value in FIELDS:
        steps.append(
            PlanStep(
                kind="tool",
                name="type",
                args={"element_id": f"auto:{auto_id}", "name": label, "text": value, "target": TARGET},
            )
        )
    submit = {"element_id": f"auto:{SUBMIT[0]}", "name": SUBMIT[1], "target": TARGET}
    if confirm_submit:
        submit["risk"] = "confirm"
    steps += [
        PlanStep(kind="tool", name="invoke", args=submit),
        PlanStep(kind="tool", name="wait", args={"seconds": 0.8}),
        PlanStep(kind="tool", name="assert_text", args={"target": TARGET, "contains": EXPECTED}),
        PlanStep(kind="done", message="form submitted"),
    ]
    return steps


def model_task(fixture_uri: str, browser: str) -> str:
    return (
        "Fill in and submit the local test form in the browser.\n"
        f"1. open_url(url='{fixture_uri}', browser='{browser}', target='{TARGET}').\n"
        f"2. type(element_id='auto:fullName', text='{NAME_VALUE}').\n"
        f"3. type(element_id='auto:email', text='{EMAIL_VALUE}').\n"
        "4. invoke(element_id='auto:submitButton').\n"
        "You may send steps 2-4 in one turn. If an auto: id is missing from the Elements list, "
        "use the name: id of the 'Full name' / 'Email' Edit and the 'Submit form' Button. "
        f"Call done when the page shows '{EXPECTED}'."
    )


async def golden_check(target: str) -> tuple[bool, str]:
    """Independent check after the run: is the expected text on the page (or in its title)?"""
    from wincua_driver import observe

    obs = await observe(target=target, include=["uia", "app"])
    app = obs.app or {}
    if any(EXPECTED in (el.name or "") for el in obs.uia):
        return True, "page text"
    if EXPECTED in str(app.get("title") or "") or EXPECTED in str(app.get("page") or ""):
        return True, "page title"
    seen = {k: app.get(k) for k in ("title", "url", "fields", "note") if app.get(k)}
    if obs.errors:
        seen["errors"] = [e.code for e in obs.errors]
    return False, f"seen {seen}"


async def write_dump(target: str, *, show: bool) -> str | None:
    from wincua_driver.backend import get_backend

    dumper = getattr(get_backend(), "dump_tree", None)
    if dumper is None:
        return None
    try:
        text = await dumper(target)
    except Exception as exc:  # diagnostics must never hide the real result
        text = f"dump_tree failed: {type(exc).__name__}: {exc}"
    if show:
        print(text)
    path = Path("recordings") / f"browser-form-{time.strftime('%Y%m%d-%H%M%S')}-uia.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return str(path)


def build_hitl(args: argparse.Namespace):
    """HITL transport for --hitl (or --provider null); None when not requested."""
    mode = args.hitl or ("auto" if args.provider == "null" else None)
    if mode is None:
        return None, None
    from wincua_hitl import from_settings, load_hitl_settings

    settings = load_hitl_settings(args.config)
    overrides = {"port": args.port, "open_browser": False if args.no_browser else None}
    if mode != "auto":
        overrides["mode"] = mode
    settings = settings.with_overrides(**overrides)
    if settings.mode == "off":
        raise ValueError("[hitl] mode is 'off'; pass --hitl ui or --hitl cli")
    return from_settings(settings), settings


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_provider_args(parser)
    parser.add_argument("--mock", action="store_true", help="Fake form on MockBackend (any OS, no desktop)")
    parser.add_argument("--hitl", choices=["auto", "ui", "cli"], default=None, help=(
        "Attach a human: ui = local browser page, cli = this terminal, auto = [hitl] mode from wincua.toml. "
        "Scripted runs then ask you to approve the submit; model runs fall back to you. "
        "Default: none (auto with --provider null)"
    ))
    parser.add_argument("--browser", default="msedge", choices=("msedge", "chrome"))
    parser.add_argument("--timeout", type=float, default=None, help="Launch wait seconds (also WINCUA_LAUNCH_TIMEOUT_S)")
    parser.add_argument("--max-turns", type=int, default=25, help="Model / human planned runs")
    parser.add_argument("--check", action="store_true", help="Probe the provider chain and exit")
    parser.add_argument("--dump-tree", action="store_true", help="Print the UIA tree after the run (written to recordings/ on failure anyway)")
    parser.add_argument("--port", type=int, default=None, help="HITL UI port (default from config, 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Print the HITL UI URL instead of opening it")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    print("browser_form_demo")
    print(f"  python:   {sys.version.split()[0]} ({sys.executable})")

    planner = args.provider or "scripted"
    cfg = None
    if planner not in ("scripted", "null") or args.check:
        cfg = load_demo_config(args)
        if cfg is None:
            return 1
    if args.check:
        from wincua.config import probe_chain

        for name, ok, detail in await probe_chain(cfg, args.provider):
            print(f"  [{'ok' if ok else 'unavailable'}] {name}: {detail}")
        return 0

    if not FIXTURE.is_file():
        print(f"Missing fixture: {FIXTURE} (run from the repo checkout)", file=sys.stderr)
        return 1
    fixture_uri = FIXTURE.resolve().as_uri()
    timeout_s = args.timeout or 45

    from wincua import Agent
    from wincua_driver import Semantic, act, is_windows
    from wincua_driver.backend import set_backend
    from wincua_providers import NullProvider, ScriptedProvider

    if args.mock:
        set_backend("mock")
        backend = "mock"
    elif not is_windows():
        print(
            "HOST-WINDOWS-ONLY: browser_form_demo drives Edge on Win11 (or pass --mock). "
            f"platform={sys.platform!r}",
            file=sys.stderr,
        )
        return 1
    else:
        from _windows_preflight import require_windows_uia_stack
        from wincua_driver.backend.windows import resolve_browser_executable

        if require_windows_uia_stack() != 0:
            return 1
        if args.timeout is not None:
            import os

            os.environ["WINCUA_LAUNCH_TIMEOUT_S"] = str(args.timeout)
        exe = resolve_browser_executable(args.browser)
        if exe is None:
            print(f"BROWSER NOT FOUND: {args.browser} is not installed under Program Files / LocalAppData.", file=sys.stderr)
            return 1
        print(f"  browser:  {exe}")
        backend = "windows"
        set_backend("windows", allow=ALLOW)

    try:
        hitl, hitl_settings = build_hitl(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    print(f"  fixture:  {fixture_uri}")
    print(f"  backend:  {backend}")
    if hitl_settings is not None:
        print(f"  hitl:     {hitl_settings.mode}" + (f" (config {hitl_settings.source})" if hitl_settings.source else ""))

    if planner == "scripted":
        provider = ScriptedProvider(
            build_browser_steps(fixture_uri, browser=args.browser, timeout_s=timeout_s, confirm_submit=hitl is not None)
        )
        task = f"Open the local form in {args.browser}, fill name and email, submit, and leave {EXPECTED!r} visible."
    elif planner == "null":
        # The human plans every step; open the page for them first.
        print(f"  opening the form in {args.browser} ... (up to {timeout_s:.0f}s)")
        res = await act(Semantic(name="open_url", args=open_args(fixture_uri, args.browser, timeout_s)))
        if not res.ok:
            print(f"open_url failed: {res.error.message if res.error else res}", file=sys.stderr)
            await _report_failure(args, backend)
            return 1
        provider = NullProvider()
        task = (
            f"In the '{TARGET}' page: type {NAME_VALUE!r} into Full name, {EMAIL_VALUE!r} into Email, "
            f"then press Submit form. Mark done when the page shows {EXPECTED!r}."
        )
    else:
        provider = args.provider
        task = model_task(fixture_uri, args.browser)

    agent = Agent(
        task=task,
        provider=provider,
        config=cfg,
        hitl=hitl,
        allow=ALLOW,
        mode="foreground",
        recorder=True,
        backend=backend,
        observe_target=TARGET,
        max_turns=args.max_turns,
    )
    print(f"  provider: {getattr(agent.provider, 'describe', lambda: agent.provider_name)()}")
    print(f"  allow:    {list(agent.allow)}")
    if planner == "null":
        print("  You plan every step in the HITL UI: Type into Full name / Email, then Submit form, then Mark done.")

    result = await agent.run()
    print_run(agent, result)

    ok, how = await golden_check(TARGET)
    if not ok or args.dump_tree:
        await _report_failure(args, backend, show=args.dump_tree, failed=not ok)
    if result.recording_path:
        print(f"  recording: {result.recording_path}")
    print(f"golden {EXPECTED!r}: {'PASS' if ok else 'FAIL'} ({how})")
    if not result.ok:
        return 2
    return 0 if ok else 3


async def _report_failure(args: argparse.Namespace, backend: str, *, show: bool = False, failed: bool = True) -> None:
    if backend == "mock" and not show:
        return
    path = await write_dump(TARGET, show=show)
    if path and failed:
        print(f"  UIA tree dump: {path}  (attach this file when reporting the failure)")
    elif path:
        print(f"  UIA tree dump: {path}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
