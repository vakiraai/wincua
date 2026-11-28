"""Console script ``wincua-mcp`` / ``python -m wincua_mcp``."""

from __future__ import annotations

import argparse
import sys

from wincua_mcp import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wincua-mcp",
        description="wincua MCP server (stdio by default). Run it on the interactive Windows desktop it drives.",
    )
    p.add_argument("--config", help="Path to wincua.toml (default: $WINCUA_CONFIG, ./wincua.toml, %%APPDATA%%\\wincua\\config.toml)")
    p.add_argument("--transport", choices=["stdio", "http"], help="Override [mcp].transport")
    p.add_argument("--host", help="HTTP bind host (default 127.0.0.1)")
    p.add_argument("--port", type=int, help="HTTP port (default [mcp].http_port = 8766)")
    p.add_argument("--backend", choices=["windows", "mock", "null"], help="Override [driver].backend")
    p.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="EXE",
        help="Add a process to the allow-list (repeatable), e.g. --allow calc --allow msedge.exe",
    )
    confirm = p.add_mutually_exclusive_group()
    confirm.add_argument("--require-confirmation", dest="require_confirmation", action="store_true", default=None)
    confirm.add_argument("--no-require-confirmation", dest="require_confirmation", action="store_false")
    p.add_argument("--print-config", action="store_true", help="Print the resolved settings and exit")
    p.add_argument("--version", action="version", version=f"wincua-mcp {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    from wincua_mcp.settings import SettingsError, load_settings

    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
    except SettingsError as exc:
        print(f"wincua-mcp: {exc}", file=sys.stderr)
        return 2
    if args.transport:
        settings.transport = args.transport
    if args.host:
        settings.http_host = args.host
    if args.port:
        settings.http_port = args.port
    if args.backend:
        settings.backend = args.backend
    if args.allow:
        settings.allow = [*settings.allow, *args.allow]
    if args.require_confirmation is not None:
        settings.require_confirmation = args.require_confirmation

    if args.print_config:
        print(
            "\n".join(
                [
                    f"config: {settings.config_path or '(none; built-in defaults)'}",
                    f"transport: {settings.transport}",
                    f"http: {settings.http_host}:{settings.http_port}",
                    f"backend: {settings.backend}",
                    f"allow: {settings.effective_allow}",
                    f"require_confirmation: {settings.require_confirmation}",
                ]
            ),
            file=sys.stderr,
        )
        return 0

    if not settings.effective_allow:
        print(
            "wincua-mcp: warning: allow-list is empty; every desktop action will be refused. "
            "Set [mcp].allow in wincua.toml or pass --allow.",
            file=sys.stderr,
        )

    from wincua_mcp.server import run

    try:
        run(settings)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
