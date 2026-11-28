#!/usr/bin/env python3
"""3D Slicer — open volume demo (Phase 2).

**Host Windows + installed 3D Slicer** for a live run. On Linux or without Slicer
this script exits with a clear message (use ``--mock`` to exercise the success
path with a fake subprocess).

Flow:
1. Resolve ``Slicer.exe`` (env or Program Files).
2. ``select_module`` → ``open_volume`` via Slicer Python API (``--python-code``).
3. Print ``golden 'volume loaded': PASS|FAIL``.

UIA fallback: not used in this demo; see ``packages/wincua-slicer/README.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    _ROOT / "packages" / "wincua-slicer" / "src",
):
    sys.path.insert(0, str(_p))

GOLDEN_LABEL = "volume loaded"


def _mock_open_volume(volume: Path) -> bool:
    from unittest.mock import MagicMock, patch

    from wincua_slicer.cli import SlicerRunResult

    if not volume.name:
        volume = Path("sample.nrrd")
    exe = Path("/fake/Slicer.exe")
    fake_result = SlicerRunResult(
        ok=True,
        exit_code=0,
        stdout=f"WINCUA_SLICER_MARKER:volume:{volume.name}\n",
        stderr="",
        command=[str(exe), "--python-code"],
    )

    proc = MagicMock()
    proc.communicate.return_value = (fake_result.stdout, "")
    proc.returncode = 0

    with patch("wincua_slicer.api.resolve_slicer_executable", return_value=exe):
        with patch("wincua_slicer.api.require_windows_for_live", return_value=None):
            with patch("wincua_slicer.api.run_python_code", return_value=fake_result):
                with patch.object(Path, "is_file", return_value=True):
                    from wincua_slicer import open_volume

                    result = open_volume(volume, module="Volumes", executable=exe)
    return result.ok and "WINCUA_SLICER_MARKER:volume:" in result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a volume in 3D Slicer via wincua-slicer")
    parser.add_argument(
        "--volume",
        required=True,
        type=Path,
        help="Path to .nrrd / .nii / .nii.gz (must exist for live run)",
    )
    parser.add_argument("--module", default="Volumes", help="Slicer module name")
    parser.add_argument("--mock", action="store_true", help="Fake subprocess (Linux CI)")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    if args.mock:
        ok = _mock_open_volume(args.volume)
        print(f"golden '{GOLDEN_LABEL}': {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    from wincua_slicer import probe_installed
    from wincua_slicer.errors import SlicerError, SlicerNotInstalledError, SlicerNotSupportedError

    info = probe_installed()
    print(f"  slicer_probe: {info}")

    try:
        from wincua_slicer import open_volume, select_module

        if not args.volume.is_file():
            print(f"error: volume not found: {args.volume}", file=sys.stderr)
            print(
                "hint: download a sample .nrrd or pass an absolute path to your data.",
                file=sys.stderr,
            )
            return 2

        select_module(args.module, timeout_s=args.timeout)
        result = open_volume(args.volume, module=args.module, timeout_s=args.timeout)
        marker_ok = "WINCUA_SLICER_MARKER:volume:" in result.stdout
        ok = result.ok and marker_ok
        print(result.stdout.strip())
        print(f"golden '{GOLDEN_LABEL}': {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    except SlicerNotInstalledError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "\nInstall 3D Slicer: https://download.slicer.org/\n"
            "Then set SLICER_EXECUTABLE to Slicer.exe and re-run.",
            file=sys.stderr,
        )
        return 3
    except SlicerNotSupportedError as exc:
        print(str(exc), file=sys.stderr)
        print("Use --mock on non-Windows hosts.", file=sys.stderr)
        return 4
    except SlicerError as exc:
        print(str(exc), file=sys.stderr)
        print(f"golden '{GOLDEN_LABEL}': FAIL")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())