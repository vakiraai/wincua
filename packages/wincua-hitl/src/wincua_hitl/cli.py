"""Console entry: ``wincua-hitl serve`` runs a sample session so you can try the UI."""

from __future__ import annotations

import argparse
import asyncio
import sys

from wincua_hitl.factory import from_settings
from wincua_hitl.settings import load_hitl_settings


def _sample_observation():
    from types import SimpleNamespace as NS

    names = [("num1Button", "One"), ("num7Button", "Seven"), ("plusButton", "Plus"), ("equalButton", "Equals")]
    uia = [NS(element_id="auto:CalculatorResults", role="Text", name="Display is 0", bounds=None, patterns=())]
    uia += [NS(element_id=f"auto:{a}", role="Button", name=n, bounds=None, patterns=("Invoke",)) for a, n in names]
    return NS(uia=uia, screenshot=None, app={"title": "Calculator (sample)", "display": "Display is 0"}, errors=[])


async def _sample_session(ui) -> int:
    obs = _sample_observation()
    task = "Sample session: no desktop is driven"
    await ui.start()
    try:
        answer = await ui.prompt(
            question="Which Calculator mode should the agent use?",
            options=["Standard", "Scientific"],
            observation=obs,
            task=task,
        )
        print(f"answer: {answer!r}")
        resp = await ui.confirm(
            action={"tool": "key", "args": {"keys": "alt+f4"}},
            reason="hotkey 'alt+f4' can close, delete or save",
            observation=obs,
            task=task,
        )
        print(f"confirm: {resp.kind if resp else None!r}")
        resp = await ui.next_step(task=task, observation=obs)
        print(f"next_step: {resp.kind if resp else None!r} {resp.action if resp else ''}")
    finally:
        await ui.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wincua-hitl")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="Run a sample HITL session (question, confirm, next step)")
    serve.add_argument("--config", default=None, help="Path to wincua.toml")
    serve.add_argument("--mode", choices=["ui", "cli"], default=None)
    serve.add_argument("--host", default=None, help="Loopback host (default from [hitl])")
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        try:
            settings = load_hitl_settings(args.config).with_overrides(
                mode=args.mode,
                host=args.host,
                port=args.port,
                open_browser=False if args.no_browser else None,
            )
        except (ValueError, FileNotFoundError) as exc:
            print(f"wincua-hitl: {exc}", file=sys.stderr)
            return 2
        if settings.mode == "off":
            settings = settings.with_overrides(mode="ui")
        try:
            return asyncio.run(_sample_session(from_settings(settings)))
        except KeyboardInterrupt:
            return 130

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
