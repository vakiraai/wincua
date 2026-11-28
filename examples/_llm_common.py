"""Shared helpers for running the demos against a real model (``--provider`` / ``--config``)."""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any


def add_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "Plan with this provider from the config (ollama | llamacpp | anthropic | openai | "
            "openai_compat | null | <custom name>) instead of the built-in script"
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to wincua.toml (default lookup: WINCUA_CONFIG, ./wincua.toml, %%APPDATA%%\\wincua\\config.toml)",
    )


def wants_model(args: argparse.Namespace) -> bool:
    if args.provider == "scripted":
        return False
    return args.provider is not None or args.config is not None


def load_demo_config(args: argparse.Namespace) -> Any | None:
    """Load and print the config; ``None`` (after printing why) on error."""
    from wincua.config import ConfigError, describe, load_config

    try:
        cfg = load_config(args.config)
        print(describe(cfg, args.provider))
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return None
    return cfg


def print_run(agent: Any, result: Any) -> None:
    for step in result.steps:
        kind = step.get("kind")
        if kind == "plan":
            calls = ", ".join(
                f"{s['name'] or s['kind']}({_short(s['args'] or {})})" for s in step.get("plan_steps", [])
            )
            note = f" error={step['error']!r}" if step.get("error") else ""
            print(f"  [turn {step['turn']}] {step['provider']}: {calls or '(no tool call)'}{note}")
        elif kind == "tool":
            status = "ok" if step.get("ok") else f"FAILED: {step.get('message')}"
            if step.get("detail_log"):
                status += f" (full detail: {step['detail_log']})"
            info = step.get("info") or {}
            if info.get("retried") or info.get("fallback"):
                how = "retried" if info.get("retried") else f"{info['fallback']} fallback"
                status += f" ({how} after {info.get('invoke_error')})"
            print(f"      -> {step.get('name')}: {status}")
        elif kind == "escalate":
            print(f"  ESCALATE: {step['reason']} -> {step.get('to') or 'stop'}")
        elif kind == "resume":
            print(f"  HUMAN: {step['instruction']!r} -> {step.get('to') or 'no model to hand back to'}")
        elif kind in {"error", "done", "abort", "ask_human"}:
            print(f"  - {step}")
    for event in getattr(agent.provider, "events", []) or []:
        print(f"  fallback: {event}")
    print(f"result.ok={result.ok} message={result.message!r}")


def _short(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in args.items() if not k.startswith("_"))[:120]


async def verify_visible(target: str, *, equals: str | None = None, contains: str | None = None) -> tuple[bool, str]:
    """Independent check after the model says done: is the expected text on screen?"""
    from wincua_driver import observe

    obs = await observe(target=target, include=["uia", "app"])
    chunks = [str(v) for v in (obs.app or {}).values() if v]
    chunks += [el.name for el in obs.uia if el.name]
    hay = "\n".join(chunks)
    if equals is not None:
        ok = bool(re.search(rf"(?:^|\D){re.escape(equals)}(?:\D|$)", hay))
    else:
        ok = contains is not None and contains in hay
    display = (obs.app or {}).get("display") or hay[:200]
    return ok, str(display)
