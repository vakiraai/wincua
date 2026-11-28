# wincua-slicer

MIT optional pack for **3D Slicer** on Windows: launch, load volumes/files, and select named modules via Slicer's Python API (`Slicer.exe --python-code`). This path is preferred over raw UIA because Slicer's Qt tree is often weak for canvas-heavy views.

## Install

```powershell
pip install -e packages/wincua-slicer
# or full stack:
pip install -e "packages/wincua[windows,slicer]"
```

## Install 3D Slicer (Win11)

1. Download the stable Windows 64-bit build: https://download.slicer.org/
2. Run the installer (e.g. into `C:\Program Files\Slicer 5.6.2\`).
3. Confirm `Slicer.exe` exists, or set:

```powershell
$env:SLICER_EXECUTABLE = "C:\Program Files\Slicer 5.6.2\Slicer.exe"
```

Discovery order: `SLICER_EXECUTABLE` → `WINCUA_SLICER_EXECUTABLE` → scan under Program Files.

## Quick API

```python
from pathlib import Path
from wincua_slicer import open_volume, select_module, launch, probe_installed

print(probe_installed())
launch()  # optional GUI
select_module("Volumes")
result = open_volume(r"C:\data\sample.nrrd")
print(result.stdout)
```

## Demo

From repo root (requires a real volume path on Windows):

```powershell
python examples/slicer_open_volume_demo.py --volume "C:\data\sample.nrrd"
```

Linux / CI (mocked subprocess):

```bash
python examples/slicer_open_volume_demo.py --mock --volume /tmp/sample.nrrd
```

## UIA / vision fallback (not in this package)

When API helpers fail (custom org Slicer build, module name mismatch):

1. Use `wincua-driver` `observe` + `dump_tree` on the main window.
2. Plan clicks via HITL or vision model — document blockers in the demo log.
3. Do **not** assume stable AutomationIds on the 3D view.

## MCP (future)

`semantic("slicer.open_volume", path=...)` will dispatch these helpers from `wincua-mcp` in a follow-up PR.