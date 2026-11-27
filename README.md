# wincua

Open-source, Windows-host, Python-first **computer-use** runtime.

Drive real desktop apps with the same API whether the planner is a local VLM
(Ollama / OpenAI-compat), a cloud computer-use model, a **scripted** plan (demos),
or a human in a small local HITL UI.

> **Host Windows only for real desktop I/O.** Observation and action use a
> pywinauto/UIA backend on Win11. CI and non-Windows hosts keep `null` / `mock`
> backends and get clear errors if `windows` is selected.

## Status

Phase 0/1 — **real Windows driver path** + scripted Calculator / browser demos.
Full VLM planning stays thin; demos succeed with `ScriptedProvider` (no LLM).

## Supported Python (important)

| Use | Python |
| --- | --- |
| **Windows host demos / UIA** | **CPython 3.11–3.13** (recommended) |
| 3.14 | Experimental — may work with recent `pywin32` / `comtypes` |
| **3.15** | **Not supported yet** — `pip show pywinauto` can succeed while `import pywinauto` fails inside pywin32/comtypes |
| CI / import-only / `--mock` | Same `requires-python`: `>=3.11,<3.15` |

Packages declare `requires-python = ">=3.11,<3.15"`.

## Packages

| Package | Import | Role |
| --- | --- | --- |
| `wincua` | `from wincua import Agent` | Agent observe/act loop + façade |
| `wincua-driver` | `wincua_driver` | observe / act (`null` \| `mock` \| `windows`) |
| `wincua-providers` | `wincua_providers` | Ollama, OpenAI-compat, Null, **Scripted** |
| `wincua-hitl` | `wincua_hitl` | Local HITL UI + `ask_human` |
| `wincua-mcp` | `wincua_mcp` | MCP server (`wincua-mcp`) |
| `wincua-excel` | — | Post-MVP pack (stub) |
| `wincua-slicer` | — | Phase 2 pack (stub) |

## Install

Always use **`python -m pip`** with the **same** interpreter that will run the demos
(`sys.executable`). Bare `pip` often targets a different env.

### Any OS (import + mock demos + tests)

```bash
python -m pip install -e packages/wincua-driver
python -m pip install -e packages/wincua-providers
python -m pip install -e packages/wincua-hitl
python -m pip install -e packages/wincua-mcp
python -m pip install -e packages/wincua
python -m pip install pytest
python -m pytest
```

Or with uv from the monorepo root: `uv sync`

### Windows 11 host (real UIA) — use Python 3.11–3.13

```powershell
# Create / activate a 3.11–3.13 venv if your default is 3.15+
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -U pip
python -m pip install -e "packages/wincua-driver[windows]"
python -m pip install -e packages/wincua-providers packages/wincua-hitl
python -m pip install -e "packages/wincua[windows]"

# Verify THIS interpreter can import (not just that the wheel is installed)
python -m pip show pywinauto pywin32 comtypes
python -c "import sys, pywinauto; print(sys.executable, sys.version); print(pywinauto.__version__)"
```

## Run demos (Win11 interactive desktop)

Use a **normal logged-on desktop** (not a headless / service / SSH-only session).
Open Calculator once from Start if it has never been launched on this account.

```powershell
cd path\to\wincua
# same venv / same python as install above
python examples/calculator_arith_demo.py
python examples/browser_form_demo.py
python examples/browser_form_demo.py --browser chrome

# Optional: longer wait if the machine is slow
python examples/calculator_arith_demo.py --timeout 60
$env:WINCUA_LAUNCH_TIMEOUT_S = "60"
```

### Launch / import troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `import pywinauto` fails but `pip show` works | Dep import (pywin32/comtypes), often **Python 3.15** | Use **3.11–3.13**; demos now print full exception chain + `sys.version` |
| Timeout, app never appears | Old `Application.start("calc.exe")` stub wait | Driver uses `calculator:` / AppsFolder launch |
| Calculator title found, no buttons | UWP: controls under ApplicationFrameHost tree | Deep UIA descendant search + `clearEntryButton` / Escape fallback |
| Browser steps ok but form empty | Edge reused a multi-tab window / infobar stole focus | `--new-window` + throwaway `--user-data-dir`; UIA ValuePattern fill with verify |
| Timeout, wrong window | Title mismatch | Errors list **visible titles** |
| Browser fails immediately | Edge/Chrome not on `PATH` | Driver resolves under Program Files |
| `file://` missing | Outside repo checkout | Demo checks `examples/fixtures/form.html` |

### Without a Windows desktop (CI / Linux / macOS)

```bash
python examples/calculator_arith_demo.py --mock
python examples/browser_form_demo.py --mock
```

`--mock` exercises the same scripted `Agent.run` loop against `MockBackend`
(no GUI). Selecting the real windows backend off-Win32 raises a clear error.

## Browser demo limits

Chromium accessibility for `file://` pages is less stable than Calculator UIA.
The demo prefers a **keyboard Tab/type/Enter** path after focusing the window.
UIA name/`aria-label` clicks are supported by the driver but may miss focus on
some Edge builds. See the module docstring in `examples/browser_form_demo.py`.

## Agent API sketch

```python
from wincua import Agent
from wincua.providers import ScriptedProvider, NullProvider, OllamaProvider
from wincua_providers.base import PlanStep

agent = Agent(
    task="Open Calculator and compute 17 + 25",
    provider=ScriptedProvider([...]),  # or OllamaProvider / NullProvider
    allow=["CalculatorApp.exe", "ApplicationFrameHost.exe", "calc.exe"],
    backend="windows",  # auto on Win32; use "mock" in tests
    mode="foreground",
)
result = await agent.run()
```

Low-level:

```python
from wincua.driver import observe, act, set_backend
set_backend("windows", allow=["msedge.exe"])
obs = await observe(target="Calculator", include=["uia", "app"])
```

## MCP

```bash
python -m pip install -e packages/wincua-mcp
wincua-mcp          # or: python -m wincua_mcp
```

Run the MCP server on the **same interactive Windows logon session** as the
desktop being driven.

## License

MIT — see [LICENSE](./LICENSE). Driver path uses [pywinauto](https://github.com/pywinauto/pywinauto) (BSD);
optional Cua wrap remains a future extra, not required.
