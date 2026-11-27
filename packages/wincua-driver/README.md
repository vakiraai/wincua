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

## Launch notes (Win11)

- **Python:** `requires-python >=3.11`, including **3.15**. Windows extras pin
  `comtypes>=1.5.0` and `pywin32>=312` (PyPI publishes `cp315-win_amd64` wheels).
  Import failures print the full exception chain + dependency probes.
- Calculator: prefer `calculator:` / `shell:AppsFolder\Microsoft.WindowsCalculator_…!App`
  over `Application.start("calc.exe")` (stub process → pywinauto timeout).
- Browsers: resolve Edge/Chrome via `PATH` then common `Program Files` locations;
  open with `--new-window` + throwaway profile.
- Timeouts: constructor `launch_timeout_s=` or env `WINCUA_LAUNCH_TIMEOUT_S` (default 45s).
- Failures include **visible window titles** so mis-matched titles are debuggable.
