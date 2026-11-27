# wincua-driver

Observe / act substrate for wincua. Public types live under `wincua_driver`;
callers should prefer the `wincua` façade.

## Backends

| Name | When | Notes |
| --- | --- | --- |
| `null` | default / CI | No desktop I/O; structured errors |
| `mock` | unit tests / `--mock` demos | Deterministic fake Calculator tree |
| `windows` | Win11 host | pywinauto UIA (`pip install -e '.[windows]'`) |

```python
from wincua_driver import set_backend, observe, act, is_windows

if is_windows():
    set_backend("windows", allow=["CalculatorApp.exe", "ApplicationFrameHost.exe"])
else:
    set_backend("mock")
```

Off Windows, `set_backend("windows")` raises a clear `RuntimeError` — it does not
silently fall back.
