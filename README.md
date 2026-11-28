# wincua

Open-source, Windows-host, Python-first **computer-use** runtime.

Drive real desktop apps with the same API whether the planner is a local VLM
(Ollama / OpenAI-compat), a cloud computer-use model, a **scripted** plan (demos),
or a human in a small local HITL UI.

> **Host Windows only for real desktop I/O.** Observation and action use a
> pywinauto/UIA backend on Win11. CI and non-Windows hosts keep `null` / `mock`
> backends and get clear errors if `windows` is selected.

## Quickstart (fresh Windows 11, about 15 minutes)

The Ollama model download (6 GB) is the slowest step: start it first, and do the
rest while it runs. Every other step takes a minute or two.

### 1. Prerequisites

| Need | Notes |
| --- | --- |
| Windows 11, logged on at the desktop | Not a service, Session 0 or SSH-only session: wincua drives the visible desktop |
| Python **3.12 or 3.13** | `winget install Python.Python.3.12` (or python.org). 3.11+ is supported, 3.15 works too (see [Supported Python](#supported-python)) |
| Git | `winget install Git.Git` |
| Microsoft Edge, Calculator | Preinstalled. Open Calculator once from Start if this account never has |
| *Optional:* [Ollama](https://ollama.com) | `winget install Ollama.Ollama`, then `ollama pull qwen2.5vl:7b`. Only for the model-planned demos |
| *Optional:* Cursor | For the MCP server (step 6) |

### 2. Clone, venv, install

```powershell
git clone https://github.com/vakiraai/wincua C:\src\wincua
cd C:\src\wincua
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e "packages/wincua-driver[windows]" -e packages/wincua-providers -e packages/wincua-hitl -e packages/wincua -e packages/wincua-mcp
```

Install the packages one by one as above. The repo root is a uv workspace
manifest, not a package, so `pip install -e .` fails (see
[Troubleshooting](#quickstart-troubleshooting)).

### 3. Config and install check

```powershell
copy wincua.example.toml wincua.toml
python scripts/quickstart_check.py
```

`wincua.toml` (git-ignored) picks Ollama `qwen2.5vl:7b` with `null` (ask the
human) as fallback, and allow-lists Calculator and `msedge.exe`. The check
prints one line per item and ends with an `mcp.json` entry for this venv:

```text
wincua quickstart check
  [ok]   python: 3.12.x (C:\src\wincua\.venv\Scripts\python.exe)
  [ok]   import wincua: C:\src\wincua\packages\wincua\src\wincua
  ...
  [ok]   import mcp: C:\src\wincua\.venv\Lib\site-packages\mcp
  [ok]   pywinauto: pywinauto 0.6.9 @ C:\src\wincua\.venv\Scripts\python.exe (3.12.x)
  [ok]   edge: C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
  [ok]   config: C:\src\wincua\wincua.toml
  [ok]   provider ollama: ollama ok at http://127.0.0.1:11434, model 'qwen2.5vl:7b' present
all required checks passed
```

`[warn]` lines (Ollama not running, model not pulled yet) don't block the next step.
`[FAIL]` lines print the fix.

### 4. Demos without a model (scripted)

```powershell
python examples/calculator_arith_demo.py --mock    # fake Calculator, no desktop
python examples/browser_form_demo.py --mock        # fake form, no desktop
python examples/calculator_arith_demo.py           # real Calculator: 17 + 25
python examples/browser_form_demo.py               # real Edge: fills and submits examples/fixtures/form.html
```

The real runs open and fill the windows through UI Automation; don't type or
click while they run. Expected last lines:

```text
result.ok=True message='17 + 25 == 42'                                  # calculator_arith_demo
golden 'OK: Ada Lovelace <ada@example.com>': PASS (page text)           # browser_form_demo
```

### 5. Demos planned by Ollama

```powershell
python examples/browser_form_demo.py --check       # probe the model chain, run nothing
python examples/calculator_llm_demo.py --provider ollama
python examples/browser_form_demo.py --provider ollama
```

`--check` should show `[ok] ollama: ollama ok at http://127.0.0.1:11434, model 'qwen2.5vl:7b' present`.
The first model load takes up to a minute. Expected output:

```text
  [turn 0] ollama: launch(app='calculator', title='Calculator')
  [turn 1] ollama: invoke(element_id='auto:clearButton'), invoke(element_id='auto:num1Button'), ...
  [turn 2] ollama: done(message='42')
result.ok=True message='42'
verify: display='Display is 42' expected 42 -> PASS                     # calculator_llm_demo

  [turn 0] ollama: open_url(url='file:///C:/src/wincua/examples/fixtures/form.html', ...)
  [turn 1] ollama: type(element_id='auto:fullName', ...), type(element_id='auto:email', ...), invoke(element_id='auto:submitButton')
  [turn 2] ollama: done(message='OK: Ada Lovelace <ada@example.com>')
golden 'OK: Ada Lovelace <ada@example.com>': PASS (page text)           # browser_form_demo
```

The model's turns can differ from run to run; the `PASS` line is what counts.
If Ollama is unavailable, the run prints `[wincua] ollama unavailable: <reason>; falling back to null`
and asks you for each step in the terminal (Ctrl+C to stop).

### 6. MCP server in Cursor

Add this to `%USERPROFILE%\.cursor\mcp.json` (merge into `mcpServers` if the file
exists). Use full paths: Cursor does not activate your venv.

```json
{
  "mcpServers": {
    "wincua": {
      "command": "C:\\src\\wincua\\.venv\\Scripts\\wincua-mcp.exe",
      "args": ["--config", "C:\\src\\wincua\\wincua.toml"]
    }
  }
}
```

Sanity check in a terminal, then restart Cursor. Cursor Settings → MCP should
list `wincua` with 5 tools (`list_windows`, `launch`, `observe`, `act`, `ask_human`).

```powershell
C:\src\wincua\.venv\Scripts\wincua-mcp.exe --config C:\src\wincua\wincua.toml --print-config
# config: C:\src\wincua\wincua.toml
# transport: stdio
# http: 127.0.0.1:8766
# backend: windows
# allow: ['CalculatorApp.exe', 'ApplicationFrameHost.exe', 'calc.exe', 'msedge.exe']
# require_confirmation: True
```

Ask Cursor's agent: *"Use wincua to open Calculator, compute 17 + 25 with the buttons,
and read the display."* It should end with `Display is 42`. Risky actions (submit,
Alt+F4, …) wait for Approve/Reject in the HITL page at `http://127.0.0.1:8765`. The
browser form prompt and more are in [packages/wincua-mcp/README.md](packages/wincua-mcp/README.md#try-it).

### Quickstart troubleshooting

| Symptom | Fix |
| --- | --- |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again |
| `error: Multiple top-level packages discovered in a flat-layout: ['apps', 'evals', 'packages']` | You ran `pip install -e .` at the repo root, which is not a package. Use the per-package install line in step 2 |
| Cursor shows the server failing with `No module named 'mcp'` (`--print-config` still works) | `mcp` isn't in the venv Cursor runs. With that venv active: `python -m pip install -e packages/wincua-mcp`, and check `command` in `mcp.json` points at the same `.venv`. If pip tries to compile `pydantic-core`, use Python 3.12 or 3.13 |
| `[unavailable] ollama: ollama: unreachable at http://127.0.0.1:11434/api/tags` | Ollama isn't running: start the Ollama app (or `ollama serve`), then `ollama list` |
| `ollama reachable at ... but model 'qwen2.5vl:7b' is not pulled` | `ollama pull qwen2.5vl:7b` (or set `model` in `[providers.ollama]` to one you have) |
| Behind a proxy: pip or `ollama pull` can't download | pip: `$env:HTTPS_PROXY = "http://proxy:8080"` before installing (or `--proxy`). Ollama downloads in its own server process: set `HTTPS_PROXY` as a user environment variable, quit Ollama from the tray and start it again |
| Behind a proxy: wincua can't reach Ollama | Requests to `127.0.0.1` / `localhost` already skip the proxy. For Ollama on another machine, add its host to `NO_PROXY` |
| `HOST-WINDOWS-ONLY: ...` | Not running on Windows; use `--mock` |
| A step fails with `window_minimized`, `window_not_foreground`, `target_obscured` (or `window_hidden` / `window_cloaked` / `target_offscreen`) | The driver already tried restoring and focusing the window once, then stopped rather than type into another window. Keep the demo window visible and in front, close whatever covers it, stay on the same virtual desktop, and don't click elsewhere during the run. Details: "Window-state checks" in [docs/config.md](docs/config.md) |
| `import pywinauto` fails | See [Launch / import troubleshooting](#launch--import-troubleshooting) |
| `golden ...: FAIL` | Attach the `recordings/browser-form-*-uia.txt` the demo names, plus its output |

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
| `wincua-hitl` | `wincua_hitl` | Local HITL UI (`ui` / `cli` / `off`) + `ask_human` / `confirm_action` |
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
python -m pip install -e "packages/wincua-driver[windows]" -e packages/wincua-providers -e packages/wincua-hitl -e packages/wincua -e packages/wincua-mcp

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
python examples/browser_form_demo.py                   # last line: golden 'OK: Ada Lovelace <ada@example.com>': PASS
python examples/browser_form_demo.py --browser chrome
python examples/browser_form_demo.py --dump-tree       # also print Edge's UIA tree (page Document + Edit values)

# Optional: longer wait if the machine is slow
python examples/calculator_arith_demo.py --timeout 60
$env:WINCUA_LAUNCH_TIMEOUT_S = "60"
```

### Human in the loop (no model)

```powershell
python examples/calculator_hitl_demo.py                 # browser UI on 127.0.0.1:8765
python examples/calculator_hitl_demo.py --hitl cli      # answer in this terminal
python examples/calculator_hitl_demo.py --provider scripted   # approve/reject the risky "=" press
python examples/browser_form_demo.py --provider null          # you fill the form step by step in the UI
python examples/browser_form_demo.py --hitl ui                # scripted; approve/reject "Submit form"
```

With `NullProvider` the agent asks you for every step: pick a Calculator button,
type a command (`invoke auto:num1Button`, `key enter`), take over, or mark done.
See [packages/wincua-hitl/README.md](packages/wincua-hitl/README.md).

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

# Ollama (default in the example): qwen2.5vl:7b, no native tools, so it runs in JSON-reply mode
ollama pull qwen2.5vl:7b
python examples/calculator_llm_demo.py --provider ollama
python examples/browser_form_demo.py --provider ollama
# ...or native tool calls (set model = "qwen2.5:7b" in [providers.ollama])
ollama pull qwen2.5:7b
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
python examples/browser_form_demo.py --provider ollama
python examples/browser_form_demo.py --provider anthropic
```

`calculator_llm_demo.py --check` probes the chain; `--mock` runs the model against
a fake Calculator on any OS. After the model calls `done`, the demo reads the
display itself and checks 42.

### Reliability gate (Phase 1: at least 4 of 5 runs)

`examples/reliability.py` runs both demos N times, each in a fresh process, and
scores them against the 80% gate. It prints a per-run table (PASS/FAIL, seconds,
exit code, failure reason) and writes `report.md`, `report.json` and one log per
run under `recordings\reliability-<time>\`. It exits 1 if a demo is below the gate.

```powershell
python examples/reliability.py                          # provider from wincua.toml, 5 runs each
python examples/reliability.py --provider ollama        # qwen2.5vl:7b
python examples/reliability.py --provider scripted      # no model: driver baseline
python examples/reliability.py --runs 20 --interleave   # section 8 metric: 20 runs, demos alternated
```

Runs are unattended: a fallback to the human fails closed unless you pass
`--allow-human`, and a pass may use at most one clarification. Before each run
it closes demo windows left by the previous one (`--keep-windows` skips this).
It kills a run after `--run-timeout` seconds (default 300). `-v` echoes each
demo's output.

Every demo run also writes `recordings\<session>.jsonl` (the demo prints its path
as `recording:`). It has one JSON line per step, written as it happens: the
observation summary (elements, errors), each model reply (`raw` and `text`) with
the parsed actions, each tool's args, result and full driver error, human
answers, and a final `result` line (or `crash`). When a run fails, send that
file together with the run's log.

### Without a Windows desktop (CI / Linux / macOS)

```bash
python examples/calculator_arith_demo.py --mock
python examples/browser_form_demo.py --mock
python examples/calculator_hitl_demo.py --mock --hitl cli
python examples/reliability.py --mock --provider scripted
```

`--mock` exercises the same scripted `Agent.run` loop against `MockBackend`
(no GUI). Selecting the real windows backend off-Win32 raises a clear error.

## Browser demo (Edge UIA)

`examples/fixtures/form.html` gives every control an HTML `id` (Edge exposes it
as the UIA AutomationId) and a `<label for>` (the Edit's Name). So the fields are
`auto:fullName` / `auto:email` / `auto:submitButton`, or `name:Full name` /
`name:Email` / `name:Submit form`.

The driver opens Edge in a new window with a throwaway profile and
`--force-renderer-accessibility`. It scopes element lookups to the page
Document under the `Chrome_RenderWidgetHostHWND` pane, so form text can't land
in the address bar or the sidebar. It fills Edits through ValuePattern (falling
back to keystrokes) and checks the value. `name:` ids prefer the Edit/Button
over a label that has the same Name. Observations of a browser window report
`page`, `url` and the current `fields` values. If a Windows run fails, the
demo writes the UIA tree to `recordings/browser-form-<time>-uia.txt`; you can
also run `python examples/_dump_uia.py "wincua form demo"` with the page open.

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

```powershell
python -m pip install -e "packages/wincua-driver[windows]" -e packages/wincua-hitl -e packages/wincua-mcp
wincua-mcp --allow calc --allow msedge.exe   # stdio; or: python -m wincua_mcp
```

Tools: `observe`, `act`, `launch`, `list_windows`, `ask_human`. Stdio by default,
streamable HTTP with `--transport http`. The allow-list is fail-closed, and risky
actions go through HITL. Config (`[mcp]` in `wincua.toml`) and ready-to-paste
Cursor / Claude Desktop snippets are in [packages/wincua-mcp/README.md](./packages/wincua-mcp/README.md).

Run the MCP server on the **same interactive Windows logon session** as the
desktop being driven.

## License

MIT — see [LICENSE](./LICENSE). Driver path uses [pywinauto](https://github.com/pywinauto/pywinauto) (BSD);
optional Cua wrap remains a future extra, not required.
