# wincua-providers

Planner providers for wincua. They need no third-party packages: HTTP goes through stdlib `urllib`.

| Provider | Endpoint |
| --- | --- |
| `OllamaProvider` | Ollama native `/api/chat` (images + tool calls; JSON-reply fallback) |
| `LlamaCppProvider` | llama.cpp `llama-server`, OpenAI-compatible `/v1` |
| `OpenAICompatProvider` | Any `/v1/chat/completions` server |
| `OpenAIProvider` | api.openai.com (`OPENAI_API_KEY`) |
| `AnthropicProvider` | Messages API tool use, optional computer use (`ANTHROPIC_API_KEY`) |
| `NullProvider` | No model: route planning to the human (HITL) |
| `ScriptedProvider` | Fixed plan for demos and tests |
| `FallbackProvider` | Try a chain in order, moving on when one is unavailable |

Usually built from `wincua.toml` via `wincua.config`. See `docs/config.md` at the repo root.
Every HTTP provider takes `transport=` so tests can replay canned responses without network access.
