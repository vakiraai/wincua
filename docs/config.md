# Configuration (`wincua.toml`)

wincua picks its planner (model provider) and driver defaults from a TOML file.
Start from [`wincua.example.toml`](../wincua.example.toml).

## Where the file is read from

First hit wins:

1. An explicit path: `load_config(path)`, `Agent(config=path)` or `--config PATH` on the demos.
2. The `WINCUA_CONFIG` environment variable.
3. `.\wincua.toml` in the current directory (git-ignored in this repo).
4. `%APPDATA%\wincua\config.toml`.

An explicit path or `WINCUA_CONFIG` that points to a missing file is an error.
With no file at all, wincua uses provider `null`: a human answers through HITL.

Check what will be used, and whether each provider is reachable:

```powershell
python -m wincua.config --probe
python -m wincua.config --config .\my.toml --provider anthropic --probe
```

`--probe` spends no tokens. It calls Ollama `/api/tags`, llama-server `/health`, `/models` on OpenAI-compatible servers, and Anthropic `/v1/models`.

## Secrets

API keys are **never** stored in the file. Each provider names the environment
variable that holds its key (`api_key_env`). A provider table containing
`api_key`, `token`, `secret` or `password` is rejected. Environment variables
override secrets only; every other setting comes from the file or CLI flags.

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:OPENAI_API_KEY = "sk-..."
```

## `[provider]`: which model plans

```toml
[provider]
default = "ollama"      # ollama | llamacpp | anthropic | openai | openai_compat | null | scripted | <custom>
fallback = ["null"]
```

The chain is `default`, then `fallback` in order. It always ends in `null`, and
`null` stops the chain. A provider is skipped when it is **unavailable**: it is
unreachable, it times out, its API key env var is unset, or it returns
401/403/404 (for example an Ollama model that has not been pulled). Once the
chain moves on, it stays on the new provider for the rest of the run. `null`
routes planning to the human via `ask_human`. With no HITL transport the run
fails closed.

`--provider NAME` (demos) or `Agent(provider="NAME")` replaces `default`; the fallback list still applies.

## `[providers.<name>]`

| Key | Used by | Notes |
| --- | --- | --- |
| `base_url` | all HTTP providers | Ollama root; llama-server root (`/v1` is added); OpenAI-compatible URL **including** `/v1` |
| `model` | all | Ollama tag, OpenAI/Anthropic model id; llama-server ignores it |
| `api_key_env` | cloud / authenticated servers | Name of the env var with the key |
| `type` | custom names | Implementation for an extra table, e.g. `type = "openai_compat"` |
| `timeout_s` | all | Default 120 |
| `temperature` | all | Default 0 |
| `max_tokens` | all | Anthropic default 1024; OpenAI sends `max_completion_tokens` |
| `send_screenshot` | all | Attach the window screenshot (default true) |
| `tool_mode` | Ollama, OpenAI-compatible | `auto` (default): native tool calls, switching to JSON replies if the server says tools are unsupported. `native` / `json` force one mode |
| `retries` | all | Retries for 429/502/503/504/529 and timeouts (default 1) |
| `num_ctx`, `keep_alive` | Ollama | Passed to Ollama options |
| `computer_use` | Anthropic | Also offer Anthropic's `computer` tool (see below) |
| `steps` | scripted | List of `{kind, name, args, message}` plan steps |

Built-in defaults match the example file, so `[providers.ollama]` can be omitted entirely.

### Provider notes

- **Ollama** uses the native `POST /api/chat` with `images` and `tools`.
  Vision models such as `qwen2.5vl` do not advertise tool support. On the first
  "does not support tools" error, wincua switches to JSON replies
  (`format: "json"`) automatically.
- **llama.cpp** (`llama-server`) uses the OpenAI-compatible `/v1/chat/completions`.
  Start it with `--jinja` for native tool calls and with a vision model plus its
  `--mmproj` for screenshots. Without them, wincua falls back to JSON replies and
  sends the UI Automation text only.
- **OpenAI-compatible** works with any `/v1/chat/completions` server: LM Studio, vLLM, LocalAI, OpenRouter, and so on.
- **OpenAI** uses `https://api.openai.com/v1` with `OPENAI_API_KEY` and function tools.
- **Anthropic** uses the Messages API with tool use and `ANTHROPIC_API_KEY`.
  With `computer_use = true` it also sends the `computer_20250124` tool (beta
  `computer-use-2025-01-24`). The screenshot is of the target window, so
  click coordinates are translated by the window origin and any downscale
  (Pillow downscales screenshots above 1280×800). `left_click`, `type`, `key`,
  `wait` and `screenshot` are supported. Other actions (drag, scroll,
  mouse_move…) are returned to the model as failed tool results. The UI
  Automation tools stay available and are preferred.

All providers see the same tools: `launch`, `focus`, `open_url`, `invoke`,
`type`, `click`, `key`, `wait`, `ask_human`, `done`, `abort`.

Each observation lists elements as `id role "name"`. The id is a stable
`auto:<AutomationId>` when the control has one, else `name:<Name>`. The Agent
cleans up what a model sends before it reaches the driver:

- An `element_id` that is a bare AutomationId, an older snapshot id (`clearButton_112`) or an element name is mapped to the matching element.
- Empty values and stray keys (`role`) are dropped.
- A `target` that is not the observed window's title is ignored.

When a tool fails, the model sees a short error. Long driver output, such as
UIA tree dumps, goes to `recordings/<session>-errors.log` when the recorder is on.

**Loop guard.** The Agent hands planning to the next provider in the chain
(ending in `null`, which asks the human) when any of these happen:

- The same actions repeat for 3 turns without the screen changing.
- 3 actions in a row fail.
- The model replies 5 times in a row without a tool call (`Agent.max_consecutive_errors`).

With no provider left, the run stops.

## `[driver]`

```toml
[driver]
backend = "windows"     # windows | mock | null (omit = auto: windows on Win32)
allow = ["CalculatorApp.exe", "ApplicationFrameHost.exe", "calc.exe"]
launch_timeout_s = 45
```

`allow` is added to the allow-list the caller passes to `Agent`. An explicit `Agent(backend=...)` wins over the file.

## Other sections

`[hitl]` and `[mcp]` belong to `wincua-hitl` and `wincua-mcp`. They read them from `WincuaConfig.raw` (or `tomllib` directly). See the example file for their keys.

## Python API

```python
from wincua import Agent, load_config

cfg = load_config()                       # lookup order above
agent = Agent(task="...", config=cfg)     # provider = [provider].default + fallback
agent = Agent(task="...", provider="anthropic")
provider = cfg.build_provider("ollama")   # FallbackProvider(ollama -> null)
```
