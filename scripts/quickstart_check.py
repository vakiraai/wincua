#!/usr/bin/env python3
"""Check a wincua quickstart install: Python, packages, Windows UIA, Edge, config, Ollama.

    python scripts/quickstart_check.py
    python scripts/quickstart_check.py --config C:\\path\\to\\wincua.toml

Prints one line per check ([ok] / [warn] / [FAIL]) and, at the end, a Cursor
mcp.json entry with this venv's paths. Exit code 1 if any check FAILs; warnings
(no wincua.toml, Ollama not running) do not fail, because the scripted and
--mock demos run without them.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (
    'python -m pip install -e "packages/wincua-driver[windows]" -e packages/wincua-providers '
    "-e packages/wincua-hitl -e packages/wincua -e packages/wincua-mcp"
)
MODULES = ("wincua", "wincua_driver", "wincua_providers", "wincua_hitl", "wincua_mcp", "mcp")


class Report:
    def __init__(self) -> None:
        self.failed = 0
        self._fixes: set[str] = set()

    def ok(self, what: str, detail: str = "") -> None:
        print(f"  [ok]   {what}" + (f": {detail}" if detail else ""))

    def warn(self, what: str, detail: str, fix: str = "") -> None:
        print(f"  [warn] {what}: {detail}" + self._fix(fix))

    def fail(self, what: str, detail: str, fix: str = "") -> None:
        self.failed += 1
        print(f"  [FAIL] {what}: {detail}" + self._fix(fix))

    def _fix(self, fix: str) -> str:
        if not fix or fix in self._fixes:
            return ""
        self._fixes.add(fix)
        return f"\n         fix: {fix}"


def check_python(r: Report) -> None:
    ver = sys.version.split()[0]
    if sys.version_info < (3, 11):
        r.fail("python", f"{ver} is too old", "install Python 3.12 or 3.13 and recreate the venv")
    elif sys.prefix == sys.base_prefix:
        r.warn("python", f"{ver} at {sys.executable} is not a venv", r"activate it: .\.venv\Scripts\Activate.ps1")
    else:
        r.ok("python", f"{ver} ({sys.executable})")


def check_imports(r: Report) -> bool:
    all_ok = True
    for mod in MODULES:
        try:
            m = importlib.import_module(mod)
        except ImportError as exc:
            all_ok = False
            r.fail(f"import {mod}", str(exc), INSTALL)
            continue
        where = Path(getattr(m, "__file__", "") or "?")
        r.ok(f"import {mod}", str(where.parent))
    return all_ok


def check_windows(r: Report) -> None:
    from wincua_driver import is_windows

    if not is_windows():
        r.warn("windows", f"platform={sys.platform!r}", "real desktop runs need Win11; use --mock here")
        return
    from wincua_driver.backend.windows import probe_pywinauto, resolve_browser_executable

    ok, detail = probe_pywinauto()
    if ok:
        r.ok("pywinauto", detail)
    else:
        r.fail("pywinauto", detail.splitlines()[0] if detail else "import failed", INSTALL)
    edge = resolve_browser_executable("msedge")
    if edge is None:
        r.fail("edge", "msedge.exe not found under Program Files / LocalAppData", "install Microsoft Edge")
    else:
        r.ok("edge", str(edge))


def check_config(r: Report, path: str | None):
    from wincua.config import ConfigError, load_config

    try:
        cfg = load_config(path)
    except ConfigError as exc:
        r.fail("config", str(exc), "fix the file, or copy wincua.example.toml again")
        return None
    if cfg.path is None:
        r.warn("config", "no wincua.toml found (model demos fall back to asking you)", "copy wincua.example.toml wincua.toml")
    else:
        r.ok("config", str(cfg.path))
    return cfg


def check_models(r: Report, cfg) -> None:
    from wincua.config import probe_chain
    from wincua_providers.fallback import short_reason

    for name, ok, detail in asyncio.run(probe_chain(cfg)):
        if cfg.provider_config(name).type == "null":
            continue
        if ok:
            r.ok(f"provider {name}", short_reason(detail))
        else:
            fix = "start Ollama, then: ollama pull qwen2.5vl:7b" if "ollama" in name else ""
            r.warn(f"provider {name}", short_reason(detail), fix)


def mcp_entry(config_path: Path | None) -> str:
    exe = Path(sys.executable).parent / ("wincua-mcp.exe" if sys.platform == "win32" else "wincua-mcp")
    if not exe.exists():
        exe = Path(shutil.which("wincua-mcp") or exe)
    server: dict = {"command": str(exe)}
    if config_path is not None:
        server["args"] = ["--config", str(config_path)]
    return json.dumps({"mcpServers": {"wincua": server}}, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None, help="Path to wincua.toml (default: the normal lookup)")
    args = parser.parse_args(argv)

    print("wincua quickstart check")
    r = Report()
    check_python(r)
    cfg = None
    if check_imports(r):
        check_windows(r)
        cfg = check_config(r, args.config)
        if cfg is not None:
            check_models(r, cfg)
    if not (ROOT / "examples" / "fixtures" / "form.html").is_file():
        r.warn("checkout", f"{ROOT} has no examples/fixtures/form.html", "run from a full git clone")

    if r.failed:
        print(f"{r.failed} check(s) failed")
        return 1
    print("all required checks passed")
    if cfg is not None:
        print("\nCursor %USERPROFILE%\\.cursor\\mcp.json entry for this venv:")
        print(mcp_entry(cfg.path.resolve() if cfg.path else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
