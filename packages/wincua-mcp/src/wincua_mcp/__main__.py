"""python -m wincua_mcp / console script wincua-mcp."""

from __future__ import annotations

import asyncio
import sys


def main() -> None:
    from wincua_mcp.server import run_stdio

    try:
        asyncio.run(run_stdio())
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
