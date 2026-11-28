# wincua-mcp

MCP server that lets coding agents (Cursor, Claude Desktop, Codex, …) drive real
Windows desktop apps through the wincua driver (UI Automation via pywinauto).
Built on the official [`mcp`](https://pypi.org/project/mcp/) Python SDK (FastMCP).

- Transport: **stdio** by default, optional **streamable HTTP** (`http://127.0.0.1:8766/mcp`).
- Safety: fail-closed **process allow-list**. Risky actions go through the **HITL** hook when `require_confirmation = true`.
- Run it on the **same interactive Windows logon session** as the desktop it drives. Services, Session 0 and SSH-only sessions cannot see the desktop.

## Tools

| Tool | What it does |
| --- | --- |
| `list_windows` | Visible top-level windows: title, process, pid, foreground, and whether each is allowed |
| `launch` | Start an allow-listed app (`app="calc"`, `app="notepad.exe"`) or open `url` in Edge/Chrome. The window becomes the current target |
| `observe` | Trimmed UIA tree (`id`, `role`, `name`, `bounds`, `patterns`) for a window, plus app state. `include_screenshot=true` attaches a PNG. Filter with `roles`, `name_contains`, `max_elements` |
| `act` | One action on the current (or given) target: `click` (element or x/y), `type` (optional `element_id`, `submit`), `invoke` (UIA Invoke), `key` (`"enter"`, `"ctrl+a"`). `dry_run=true` checks policy only |
| `ask_human` | Ask the person at the desktop via the wincua HITL channel. Returns `answered: false` on timeout or when no HITL UI is available |

Element ids come from `observe`, and they stay valid across later observations:
`auto:<AutomationId>` when the control has one (`auto:num7Button` in Calculator,
`auto:fullName` for an HTML `id="fullName"` in Edge), otherwise `name:<Name>`
(`name:Submit form`), otherwise a per-observation snapshot id. When a label and its
input share a Name, `name:` refers to the input. `type` resolves `name:` ids to an Edit
and `invoke` resolves them to a Button. In Edge/Chrome, lookups are limited to the page
Document, so the address bar is never targeted. Browser observations also report
`app.page`, `app.url` and `app.fields` (current Edit values). If the page retitles
the window (for example on submit), the current target follows the same window. Errors come back as `{"ok": false, "error": {"code", "message"}}`, with codes
such as `allow_list_empty`, `allow_list_denied`, `window_not_found`,
`confirmation_denied` and `backend_unavailable`.

### Allow-list

The effective allow-list is `[driver].allow` plus `[mcp].allow` plus any `--allow` flags.
**If it is empty, every desktop call is refused.** It is enforced on:

- `launch`: the app's executable name, or the browser for `url`.
- `observe` / `act`: the process that owns the target window.

Entries are executable basenames, matched case-insensitively. Full paths are
reduced to the basename, and `edge`/`chrome` map to `msedge.exe`/`chrome.exe`.
Any of `calc`, `calc.exe` or `CalculatorApp.exe` allows the Store Calculator,
whose window belongs to `ApplicationFrameHost.exe`. Other UWP apps stay denied
unless you list `ApplicationFrameHost.exe` itself.

### Risky actions and HITL

These actions count as risky:

- `type` with `submit=true`.
- `click` by raw x/y.
- `click` / `invoke` on a control whose name looks destructive or committing (submit, send, delete, remove, pay, buy, install, close, sign out, …).
- `Delete`, `Alt+F4`, Windows-key chords, and Ctrl/Alt chords other than editing ones (`ctrl+a/c/v/x/z/y/f/l`).

With `require_confirmation = true` (the default), a risky action blocks on
`wincua_hitl.ask_human(..., risk="confirm")` until the human answers. Only
`approve` (or yes/ok/allow) runs it. Anything else, a timeout, or no HITL
channel denies it. If `wincua_hitl` isn't installed, a stub hook is used, and it
always denies.

With `wincua-hitl` installed, the question appears as an Approve/Reject card in the
local HITL page (`http://127.0.0.1:8765`, opened in your browser). `[hitl] mode = "cli"`
is replaced by that page, because stdin is the MCP channel. For trusted local demos
you can set `require_confirmation = false` or pass `--no-require-confirmation`.

## Configuration (`[mcp]` in `wincua.toml`)

Lookup order (first hit wins):

1. `--config PATH`
2. `%WINCUA_CONFIG%`
3. `.\wincua.toml` in the server's working directory
4. `%APPDATA%\wincua\config.toml`

With no file, the built-ins apply: stdio, windows backend, empty allow-list.

```toml
[driver]
backend = "windows"            # windows | mock | null
allow = ["calc.exe", "msedge.exe"]
launch_timeout_s = 45

[mcp]
transport = "stdio"            # stdio | http (streamable HTTP at /mcp)
http_host = "127.0.0.1"        # keep loopback; the server has no auth
http_port = 8766
allow = []                     # extra allow-list entries for MCP sessions
require_confirmation = true    # route risky act calls through HITL
confirm_timeout_s = 120        # seconds to wait for approval, then deny
max_elements = 150             # default cap on observe elements
```

CLI overrides: `--transport`, `--host`, `--port`, `--backend`, `--allow EXE`
(repeatable), `--require-confirmation` / `--no-require-confirmation`. Run
`wincua-mcp --print-config` to see the resolved settings without starting the server.

## Windows setup

Requirements: Windows 11, Python 3.11 or newer, and an interactive logged-on desktop.

```powershell
git clone https://github.com/vakiraai/wincua C:\src\wincua
cd C:\src\wincua
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e "packages/wincua-driver[windows]" -e packages/wincua-hitl -e packages/wincua-mcp

# Config with an allow-list (adjust as needed)
New-Item -ItemType Directory -Force "$env:APPDATA\wincua" | Out-Null
@"
[driver]
backend = "windows"
allow = ["calc.exe", "msedge.exe"]

[mcp]
require_confirmation = true
"@ | Set-Content "$env:APPDATA\wincua\config.toml"

wincua-mcp --print-config        # sanity check
```

If `pip` tries to compile `pydantic-core` (a dependency of `mcp`), your Python
version has no wheel yet; use Python 3.12 or 3.13 for the MCP venv.

If the server fails with `No module named 'mcp'` (`--print-config` still works without it), reinstall in the same venv: `python -m pip install -e packages/wincua-mcp`.

### Cursor (`mcp.json`)

Global: `%USERPROFILE%\.cursor\mcp.json`, or per project: `.cursor\mcp.json`.
Use the full path to the venv's `wincua-mcp.exe`, because Cursor does not activate your venv.

```json
{
  "mcpServers": {
    "wincua": {
      "command": "C:\\src\\wincua\\.venv\\Scripts\\wincua-mcp.exe",
      "args": ["--config", "C:\\Users\\YOU\\AppData\\Roaming\\wincua\\config.toml"]
    }
  }
}
```

Streamable HTTP alternative: start `wincua-mcp --transport http` in a terminal on
the desktop session, then:

```json
{
  "mcpServers": {
    "wincua": { "url": "http://127.0.0.1:8766/mcp" }
  }
}
```

### Claude Desktop (Windows)

Edit `%APPDATA%\Claude\claude_desktop_config.json` (Claude → Settings → Developer → Edit Config), then fully quit and reopen Claude:

```json
{
  "mcpServers": {
    "wincua": {
      "command": "C:\\src\\wincua\\.venv\\Scripts\\wincua-mcp.exe",
      "args": ["--config", "C:\\Users\\YOU\\AppData\\Roaming\\wincua\\config.toml"]
    }
  }
}
```

Equivalent without the `.exe` shim: `"command": "C:\\src\\wincua\\.venv\\Scripts\\python.exe", "args": ["-m", "wincua_mcp", "--config", "..."]`.

### Try it

Ask the agent: *"Use wincua to open Calculator, compute 17 + 25 with the buttons, and read the display."*
Expected calls:

1. `launch(app="calc")`
2. `observe(roles=["Button"])`
3. `act(action="invoke", element_id="auto:num1Button")` and so on
4. `observe()`, where `app.display` reads `Display is 42`

#### Browser form (MVP demo A)

The allow-list needs `msedge.exe` (it is in `wincua.example.toml` and in the setup
snippet above). Use the absolute path of your checkout. Paste this prompt into Cursor
agent chat:

> Use the wincua MCP tools. Open `C:\src\wincua\examples\fixtures\form.html` in Edge with
> `launch(url=..., title="wincua form demo")`. Observe the page with
> `roles=["Edit","Button"]`. Type `Ada Lovelace` into the Full name field and
> `ada@example.com` into the Email field, using the element ids from observe. Then invoke
> the Submit form button; it needs my approval in the wincua HITL page. Finally observe
> again and tell me the exact result text on the page.

Expected calls:

1. `launch(url="C:\\src\\wincua\\examples\\fixtures\\form.html", title="wincua form demo")`. The driver turns the path into `file:///…`, opens a new Edge window with a throwaway profile, and waits for the page Document.
2. `observe(roles=["Edit","Button"])` lists `auto:fullName` (Edit "Full name"), `auto:email` (Edit "Email") and `auto:submitButton` (Button "Submit form").
3. `act(action="type", element_id="auto:fullName", text="Ada Lovelace")` and `act(action="type", element_id="auto:email", text="ada@example.com")`. Each returns `info.verified` with the value read back from the field.
4. `act(action="invoke", element_id="auto:submitButton")`. This is risky, so the HITL page shows an Approve/Reject card. After Approve, the result has `confirmation.approved: true`.
5. `observe(name_contains="OK:")` shows the element `OK: Ada Lovelace <ada@example.com>`, and `app.url` ends in `#submitted`.

If step 2 lists no Edits, the page's accessibility tree is not ready (or not where the
driver looks). Run `python examples/_dump_uia.py "wincua form demo"` with the page still
open, and attach the output.

## Development

```bash
python -m pytest packages/wincua-mcp     # mock backend + in-process MCP client, stdio round-trip
wincua-mcp --backend mock --allow calc --allow msedge.exe   # poke at it from any OS (fake Calculator + form)
```
