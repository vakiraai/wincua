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

### Any OS (import + mock demos + tests)

```bash
pip install -e packages/wincua-driver
pip install -e packages/wincua-providers
pip install -e packages/wincua-hitl
pip install -e packages/wincua-mcp
pip install -e packages/wincua
pip install pytest
pytest
```

Or with uv from the monorepo root: `uv sync`

### Windows 11 host (real UIA)

```bash
# pywinauto + Pillow for UIA / screenshots
pip install -e "packages/wincua-driver[windows]"
pip install -e packages/wincua-providers
pip install -e packages/wincua-hitl
pip install -e "packages/wincua[windows]"
```

Equivalent meta extras: `pip install -e ".[windows]"` from the repo root after
editable installs of workspace members, or `pip install -e "packages/wincua[windows]"`.

## Run demos (Win11 interactive desktop)

Use a **normal logged-on desktop** (not a headless service session). Calculator
and Edge/Chrome must be allow-listed (demos set this for you).

```powershell
cd path\to\wincua

# Calculator: launch, clear, 17+25=, read display, assert 42
python examples/calculator_arith_demo.py

# Browser: open local file:// form, Tab/type fill + submit
python examples/browser_form_demo.py
python examples/browser_form_demo.py --browser chrome
```

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
pip install -e packages/wincua-mcp
wincua-mcp          # or: python -m wincua_mcp
```

Run the MCP server on the **same interactive Windows logon session** as the
desktop being driven.

## License

MIT — see [LICENSE](./LICENSE). Driver path uses [pywinauto](https://github.com/pywinauto/pywinauto) (BSD/MIT-friendly stack); optional Cua wrap remains a future extra, not required.
