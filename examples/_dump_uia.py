#!/usr/bin/env python3
"""Dump the UIA tree for a top-level window (Win11 diagnostics).

Usage (on the Windows host with the app open)::

    python examples/_dump_uia.py "Calculator"

Prints:
  - matching top-level window trees (depth ~6)
  - every top-level window owned by CalculatorApp.exe

Paste the full output when reporting Calculator / UIA lookup failures.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages" / "wincua-driver" / "src"),
    str(ROOT / "packages" / "wincua" / "src"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "title",
        nargs="?",
        default="Calculator",
        help='Window title substring to match (default: "Calculator")',
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=6,
        help="Max UIA tree depth (default: 6)",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=250,
        help="Max nodes per tree (default: 250)",
    )
    args = parser.parse_args(argv)

    from wincua_driver.backend.windows import (
        describe_uia_element,
        format_uia_tree,
        is_windows,
        list_toplevel_by_process,
        match_window_title,
        probe_pywinauto,
        windows_unavailable_error,
    )

    if not is_windows():
        err = windows_unavailable_error("examples/_dump_uia.py")
        print(err.message, file=sys.stderr)
        return 2

    ok, detail = probe_pywinauto()
    if not ok:
        print(detail, file=sys.stderr)
        return 1

    from pywinauto import Application, Desktop

    title = args.title
    print(f"=== top-level windows matching title={title!r} ===")
    desktop = Desktop(backend="uia")
    matched = 0
    try:
        windows = desktop.windows()
    except Exception as exc:
        print(f"Desktop.windows() failed: {exc}", file=sys.stderr)
        return 1

    for w in windows:
        try:
            wtitle = w.window_text() or ""
        except Exception:
            continue
        if not match_window_title(wtitle, title):
            continue
        matched += 1
        meta = describe_uia_element(w)
        print(
            f"\n--- match[{matched}] title={wtitle!r} "
            f"proc={meta['process']!r} pid={meta['pid']} "
            f"class={meta['class_name']!r} ---"
        )
        print(format_uia_tree(w, max_depth=args.depth, max_nodes=args.max_nodes))

    if matched == 0:
        print("(no title matches)")
        print("Visible titles:")
        for w in windows:
            try:
                print(f"  - {w.window_text()!r}")
            except Exception:
                continue

    print("\n=== CalculatorApp.exe top-level windows ===")
    calc_wins = list_toplevel_by_process("CalculatorApp.exe")
    if not calc_wins:
        print("(none via Desktop filter)")
        # Also try Application.connect(path=...)
        try:
            app = Application(backend="uia").connect(path="CalculatorApp.exe", timeout=3)
            try:
                for w in app.windows():
                    meta = describe_uia_element(w)
                    calc_wins.append((w, meta))
            except Exception as exc:
                print(f"Application.windows() failed: {exc}")
            try:
                tw = app.top_window()
                meta = describe_uia_element(tw)
                calc_wins.append((tw, meta))
            except Exception:
                pass
        except Exception as exc:
            print(f"Application.connect(path='CalculatorApp.exe') failed: {exc}")

    seen: set[str] = set()
    idx = 0
    for w, meta in calc_wins:
        try:
            key = f"{int(w.handle)}"
        except Exception:
            key = f"{meta.get('pid')}:{meta.get('name')}:{id(w)}"
        if key in seen:
            continue
        seen.add(key)
        idx += 1
        print(
            f"\n--- CalculatorApp[{idx}] title={meta['name']!r} "
            f"proc={meta['process']!r} pid={meta['pid']} "
            f"class={meta['class_name']!r} ---"
        )
        print(format_uia_tree(w, max_depth=args.depth, max_nodes=args.max_nodes))

    if idx == 0:
        print("(no CalculatorApp.exe windows found — is Calculator running?)")

    print("\n=== ApplicationFrameHost.exe windows titled Calculator ===")
    afh = 0
    for w, meta in list_toplevel_by_process("ApplicationFrameHost.exe"):
        if not match_window_title(meta.get("name") or "", "Calculator"):
            continue
        afh += 1
        print(
            f"\n--- ApplicationFrameHost[{afh}] title={meta['name']!r} "
            f"pid={meta['pid']} class={meta['class_name']!r} ---"
        )
        print(format_uia_tree(w, max_depth=args.depth, max_nodes=args.max_nodes))
    if afh == 0:
        print("(none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
