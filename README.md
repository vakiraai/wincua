# wincua

Open-source, Windows-host, Python-first **computer-use** runtime.

Drive real desktop apps with the same API whether the planner is a local VLM
(Ollama / OpenAI-compat), a cloud computer-use model, a **scripted** plan (demos),
or a human in a small local HITL UI.

> **Host Windows only for real desktop I/O.** Observation and action use a
> pywinauto/UIA backend on Win11. CI and non-Windows hosts keep `null` / `mock`
> backends and get clear errors if `windows` is selected.

## Status

Phase 0/1 — **real Windows driver path** + scripted Calculator / browser demos,
plus **real model providers** (Ollama, llama.cpp, OpenAI-compatible, OpenAI,
Anthropic) chosen through [`wincua.toml`](./docs/config.md). Demos stay scripted
by default (`ScriptedProvider`, no LLM); `--provider` / `--config` plan with a model.

## Supported Python

Packages declare `requires-python = ">=3.11"` (includes **3.15**).

| Use | Notes |
| --- | --- |
| Windows host UIA (3.11–3.14) | `pywinauto` + `comtypes` + `pywin32` via `[windows]` extra |
| **Windows host UIA on 3.15** | Requires **`comtypes>=1.5.0`** and **`pywin32>=312`** (PyPI has `cp315-win_amd64` wheels). Older comtypes/pywin32 can make `import pywinauto` fail even when `pip show pywinauto` succeeds |
| CI / `--mock` | No Windows deps needed |

PyPI check (Nov 2026): `pywin32==312` ships `pywin32-312-cp315-cp315-win_amd64.whl`; `comtypes==1.5.0` is pure-Python with explicit 3.15 support; `pywinauto==0.6.9` is `py2.py3-none-any`; `Pillow>=10` has cp315 Windows wheels.

## Packages

| Package | Import | Role |
| --- | --- | --- |
| `wincua` | `from wincua import Agent` | Agent observe/act loop + façade |
| `wincua-driver` | `wincua_driver` | observe / act (`null` \| `mock` \| `windows`) |
| `wincua-providers` | `wincua_providers` | Ollama, llama.cpp, OpenAI-compat, OpenAI, Anthropic, Null, Scripted (stdlib HTTP, no extra deps) |
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

### Windows 11 host (real UIA) — including Python 3.15

```powershell
# Use whatever 3.11+ you prefer (3.15.x is fine with the pins below)
py -3.15 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -U pip
python -m pip install -e "packages/wincua-driver[windows]"
python -m pip install -e packages/wincua-providers packages/wincua-hitl
python -m pip install -e "packages/wincua[windows]"

# If upgrading an older env that already had pywinauto:
python -m pip install -U "comtypes>=1.5.0" "pywin32>=312" "pywinauto>=0.6.9"
# Optional if comtypes gen cache is corrupt:
python -m comtypes.clear_cache

# Verify THIS interpreter can import (not just that the wheel is installed)
python -m pip show pywinauto pywin32 comtypes
python -c "import sys, pywinauto, comtypes; print(sys.executable); print(sys.version); print('pywinauto', pywinauto.__version__); print('comtypes', comtypes.__version__)"
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
| `import pywinauto` fails but `pip show` works | Stale comtypes (&lt;1.5.0) or pywin32 (&lt;312) on 3.15 | `python -m pip install -U "comtypes>=1.5.0" "pywin32>=312"`; demos print full exception chain + dep probes |
| Timeout, app never appears | Old `Application.start("calc.exe")` stub wait | Driver uses `calculator:` / AppsFolder launch |
| Calculator title found, no buttons | UWP: controls under ApplicationFrameHost tree | Deep UIA descendant search + `clearEntryButton` / Escape fallback |
| Browser steps ok but form empty | Edge reused a multi-tab window / infobar stole focus | `--new-window` + throwaway `--user-data-dir`; UIA ValuePattern fill with verify |
| Timeout, wrong window | Title mismatch | Errors list **visible titles** |
| Browser fails immediately | Edge/Chrome not on `PATH` | Driver resolves under Program Files |
| `file://` missing | Outside repo checkout | Demo checks `examples/fixtures/form.html` |

## Run with a real model

Pick the provider in `wincua.toml` (copy [`wincua.example.toml`](./wincua.example.toml);
full reference: [docs/config.md](./docs/config.md)). If the chosen provider is
unreachable, wincua tries `[provider].fallback`, ending in `null` (ask the human).

```powershell
copy wincua.example.toml wincua.toml
python -m wincua.config --probe            # which file, which chain, what is reachable

# Ollama (default in the example): a text model with native tool calls
ollama pull qwen2.5:7b
python examples/calculator_llm_demo.py --provider ollama
# ...or vision + tools
ollama pull qwen3-vl:8b
python examples/calculator_llm_demo.py --provider ollama_vision

# llama.cpp — llama-server with tool calls (--jinja) and vision (mmproj via -hf)
llama-server -hf ggml-org/Qwen2.5-VL-7B-Instruct-GGUF --jinja --port 8080
python examples/calculator_llm_demo.py --provider llamacpp

# Cloud — keys come from env vars, never from the file
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python examples/calculator_llm_demo.py --provider anthropic
$env:OPENAI_API_KEY = "sk-..."
python examples/calculator_llm_demo.py --provider openai

# The scripted demos take the same flags
python examples/calculator_arith_demo.py --provider ollama
python examples/browser_form_demo.py --provider anthropic
```

`calculator_llm_demo.py --check` probes the chain; `--mock` runs the model against
a fake Calculator on any OS. After the model calls `done`, the demo reads the
display itself and checks 42.

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
    provider=ScriptedProvider([...]),  # or OllamaProvider(), "anthropic", or None = wincua.toml
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
