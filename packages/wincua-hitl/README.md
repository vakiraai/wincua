# wincua-hitl

Human-in-the-loop for wincua: one channel for "no model is planning",
questions, and risky-action approval. Stdlib only (no web framework).

| Mode | What you get |
| --- | --- |
| `ui` (default) | Local web page on `http://127.0.0.1:8765/?token=...`: current screenshot, observation (app state, errors, UIA elements), the question or proposed action, and controls |
| `cli` | Same requests in the terminal on stdin/stdout; screenshots saved to `%TEMP%\wincua-hitl\` |
| `off` | Never asks; every request fails closed (`None` / `False`) |

In the UI you can answer a question, type an instruction or command, pick the
next action from the element grid, approve or reject a risky action, take over
the desktop and resume, mark the task done, or abort. Unanswered requests time
out (`timeout_s`, default 300) and fail closed.

## API

```python
from wincua_hitl import ask_human, confirm_action, LocalUI

answer = await ask_human("Which window holds the form?", options=["Edge", "Chrome"],
                         screenshot=obs.screenshot, observation=obs)      # str | None
ok = await confirm_action({"tool": "key", "args": {"keys": "alt+f4"}},
                          reason="closes the app", observation=obs)       # bool
```

Both use the process default transport (built from `[hitl]` on first use)
unless you pass `ui=`. `ui` accepts a transport (`LocalUI`, `CliUI`, `OffUI`)
or a mode name (`"ui"`, `"cli"`, `"off"`, `"auto"`). `confirm_action` returns
True only on explicit approval; call `transport.confirm(...)` to get the full
`HumanResponse` (approve / reject / takeover / None).

Transports also expose `next_step(task=, observation=)` and `takeover(...)`,
which the agent loop uses.

## Agent integration

```python
from wincua import Agent
from wincua_providers import NullProvider

agent = Agent(task="compute 17 + 25 in Calculator", provider=NullProvider(), hitl="auto")
```

- `hitl=None` (default): no human; HITL requests fail closed.
- `hitl="auto"`: `[hitl]` from `wincua.toml`; `"ui"` / `"cli"` / `"off"` force a mode.
- `NullProvider` asks for **every step**. Free text that is not a command is
  passed to the planner as an instruction (with no model it is not executed,
  and the next prompt says so).
- Planner steps that look risky (destructive hotkeys, `submit=True`, semantic
  pack actions, controls named delete/send/submit/close/..., or `risk: "confirm"`
  on the step) need approval when `confirm_risky_actions` is on. Reject skips
  the step; take-over hands you the desktop; timeout aborts the run.
  Steps the human picked are not re-confirmed.

Commands: `invoke <id>`, `click <id>` / `click <x> <y>`, `type <text>`,
`type @<id> <text>`, `key <keys>`, `wait <s>`, `launch <app>`, `focus <title>`,
`observe`, plus `done`, `abort`, `takeover`, or a suggestion number (CLI).

## Config (`[hitl]` in `wincua.toml`)

```toml
[hitl]
mode = "ui"                  # ui | cli | off
host = "127.0.0.1"           # loopback only; anything else is rejected
port = 8765                  # falls back to a free port if taken; 0 = always pick
confirm_risky_actions = true
timeout_s = 300              # fail closed after this long
open_browser = true          # open the UI page when it starts
```

Parsed by `wincua_hitl.settings.load_hitl_settings()` with the shared lookup
order: explicit path, `WINCUA_CONFIG`, `./wincua.toml`,
`%APPDATA%\wincua\config.toml`. No file means the defaults above.

## Security

- Binds only to loopback (`127.0.0.1`, `::1`, `localhost`); other hosts raise.
- A random per-session token (`secrets.token_urlsafe`) gates everything except
  the static JS/CSS. The page needs `?token=`, which it then strips from the
  address bar; the JSON API only accepts the `X-Wincua-Token` header, so other
  sites in your browser cannot read state or post answers.
- `Host` must be a loopback name on the bound port (blocks DNS rebinding).
- Strict CSP, `no-store`, `no-referrer`, `X-Frame-Options: DENY`; JSON-only
  POST bodies capped at 64 KB. Credential answers are redacted in history.

## Try it without driving anything

```bash
wincua-hitl serve              # sample question, confirm and next-step in the browser
wincua-hitl serve --mode cli
```
