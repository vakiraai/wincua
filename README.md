# wincua

Open-source, Windows-host, Python-first **computer-use** runtime.

Drive real desktop apps with the same API whether the planner is a local VLM
(Ollama / OpenAI-compat), a cloud computer-use model, or a human in a small
local HITL UI.

> **Host Windows only.** Observation and action require an interactive Windows
> desktop session. CI and non-Windows hosts can import stubs with the `null` /
> `mock` driver backends.

## Status

Phase 0/1 **skeleton** — public API stubs, package layout, MCP entrypoint stub,
and demo scripts that document the intended flow. Real UIA / screenshot /
provider wiring lands in the Phase 0 spike.

GitHub remote is **deferred**; this tree is a local git repo for now.

## Packages

| Package | Import | Role |
| --- | --- | --- |
| `wincua` | `from wincua import Agent` | Agent loop + public façade |
| `wincua-driver` | `wincua_driver` | observe / act substrate |
| `wincua-providers` | `wincua_providers` | Ollama, OpenAI-compat, Null |
| `wincua-hitl` | `wincua_hitl` | Local HITL UI + `ask_human` |
| `wincua-mcp` | `wincua_mcp` | MCP server (`wincua-mcp`) |
| `wincua-excel` | — | Post-MVP pack (stub) |
| `wincua-slicer` | — | Phase 2 pack (stub) |

## Quick install (editable, later on Windows host)

From the monorepo root (requires [uv](https://docs.astral.sh/uv/) or pip):

```bash
# with uv (recommended)
uv sync

# or pip editable installs
pip install -e packages/wincua-driver
pip install -e packages/wincua-providers
pip install -e packages/wincua-hitl
pip install -e packages/wincua-mcp
pip install -e "packages/wincua[local,mcp]"
```

Extras on the meta package: `[local]`, `[cloud]`, `[mcp]`, `[excel]`, `[slicer]`, `[all]`.

## MCP

```bash
pip install -e packages/wincua-mcp
wincua-mcp          # or: python -m wincua_mcp
```

Run the MCP server on the **same interactive Windows logon session** as the
desktop being driven.

## Examples (host Windows)

```bash
python examples/browser_form_demo.py
python examples/calculator_arith_demo.py
```

## Tests

```bash
pytest
```

Minimal import smoke tests run without a Cua install or Windows desktop.

## License

MIT — see [LICENSE](./LICENSE).
