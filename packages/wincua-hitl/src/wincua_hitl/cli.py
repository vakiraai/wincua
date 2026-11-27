"""Console entry: wincua-hitl serve (stub)."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wincua-hitl")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="Start local HITL UI (stub)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        print(
            f"wincua-hitl serve stub: would bind http://{args.host}:{args.port} "
            "(FastAPI+static not wired yet)",
            file=sys.stderr,
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
