"""Windows UIA backend via pywinauto (MIT). Host Win32 only."""

from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import unquote, urlparse

from wincua_driver.types import (
    Action,
    ActionResult,
    Click,
    ElementRef,
    ImageRef,
    Invoke,
    Key,
    Observation,
    Semantic,
    StructuredError,
    Type,
)

# Win11 Store Calculator AUMID (stable across consumer installs)
_CALC_AUMID = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
_DEFAULT_LAUNCH_TIMEOUT_S = 45.0
_DEFAULT_CONTROL_TIMEOUT_S = 10.0

_BROWSER_CANDIDATES: dict[str, tuple[str, ...]] = {
    "msedge.exe": (
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
    ),
    "chrome.exe": (
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ),
}


def is_windows() -> bool:
    return sys.platform == "win32" or platform.system() == "Windows"


def windows_unavailable_error(what: str = "WindowsBackend") -> StructuredError:
    return StructuredError(
        code="not_windows",
        message=(
            f"{what} requires an interactive Windows (win32) desktop session. "
            f"Current platform={platform.system()!r}. "
            "Use set_backend('null') or set_backend('mock') on CI/non-Windows."
        ),
        details={"platform": platform.system(), "sys_platform": sys.platform},
    )


# Host UIA stack (pywinauto + pywin32/comtypes) is reliable on CPython 3.11–3.13.
# 3.14 may work with recent wheels; 3.15 often breaks `import pywinauto` via deps.
_SUPPORTED_WINDOWS_PYTHON = "CPython 3.11–3.13 (3.14 experimental; 3.15 not supported yet)"


def _exception_chain(exc: BaseException) -> list[BaseException]:
    seen: set[int] = set()
    out: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def format_pywinauto_import_error(cause: BaseException) -> str:
    """Full diagnostic when ``import pywinauto`` fails (missing package OR dep crash)."""
    import traceback
    from importlib.util import find_spec

    exe = sys.executable
    quoted = f'"{exe}"' if " " in exe else exe
    py_ver = sys.version.replace("\n", " ")
    major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"

    spec = None
    try:
        spec = find_spec("pywinauto")
    except Exception as spec_exc:  # pragma: no cover - defensive
        spec = None
        spec_note = f"find_spec('pywinauto') also failed: {spec_exc!r}"
    else:
        spec_note = (
            f"find_spec('pywinauto') = {spec.origin!r}"
            if spec is not None
            else "find_spec('pywinauto') = None (package not on sys.path)"
        )

    chain = _exception_chain(cause)
    chain_lines = "\n".join(f"  - {type(e).__name__}: {e!r}" for e in chain)
    tb = "".join(traceback.format_exception(type(cause), cause, cause.__traceback__))

    missing_pkg = (
        isinstance(cause, ModuleNotFoundError)
        and getattr(cause, "name", None) == "pywinauto"
        and spec is None
    )

    if missing_pkg:
        headline = (
            "pywinauto is not installed for this interpreter "
            "(ModuleNotFoundError for 'pywinauto')."
        )
        remedy = (
            "Install into THIS same interpreter (prefer python -m pip, never bare pip):\n"
            f"  {quoted} -m pip install \"pywinauto>=0.6.8\" \"Pillow>=10.0\" "
            "\"comtypes>=1.4.8\" pywin32\n"
            f"  {quoted} -m pip install -e \"packages/wincua-driver[windows]\"\n"
            f"  {quoted} -m pip install -e \"packages/wincua[windows]\"\n"
            "Verify:\n"
            f"  {quoted} -m pip show pywinauto pywin32 comtypes\n"
            f"  {quoted} -c \"import pywinauto; print(pywinauto.__version__)\""
        )
    else:
        headline = (
            "pywinauto is present but `import pywinauto` failed — usually a "
            "dependency import error (pywin32 / comtypes), not a missing pip install."
        )
        remedy = (
            "Inspect the exception chain below. On Python 3.15, comtypes <1.5.0 and "
            "older pywin32 builds are known to break; the supported host path is "
            f"{_SUPPORTED_WINDOWS_PYTHON}.\n"
            "Options:\n"
            f"  1) Prefer a 3.11–3.13 venv and reinstall extras with that interpreter.\n"
            f"  2) Try upgrading deps in-place (may still fail on 3.15):\n"
            f"       {quoted} -m pip install -U \"comtypes>=1.5.0\" \"pywin32>=308\"\n"
            f"  3) Re-run the import to see the raw traceback:\n"
            f"       {quoted} -c \"import pywinauto\""
        )

    version_warn = ""
    if sys.version_info >= (3, 15):
        version_warn = (
            f"\nWARNING: CPython {major_minor} is outside the supported Windows host "
            f"range ({_SUPPORTED_WINDOWS_PYTHON}).\n"
        )

    return (
        f"{headline}\n"
        f"{version_warn}"
        f"  sys.executable = {exe}\n"
        f"  sys.version    = {py_ver}\n"
        f"  {spec_note}\n"
        f"Exception chain:\n{chain_lines}\n"
        f"Traceback:\n{tb}"
        f"{remedy}"
    )


def ensure_pywinauto() -> None:
    """Import pywinauto or raise RuntimeError with full diagnostics (never swallow)."""
    try:
        import pywinauto  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(format_pywinauto_import_error(exc)) from exc


def probe_pywinauto() -> tuple[bool, str]:
    """Return (ok, detail). detail is version string or full import-failure diagnostics."""
    try:
        import pywinauto

        ver = getattr(pywinauto, "__version__", "unknown")
        return True, f"pywinauto {ver} @ {sys.executable} ({sys.version.split()[0]})"
    except ImportError as exc:
        return False, format_pywinauto_import_error(exc)


def match_window_title(title: str, target: str) -> bool:
    """True if a window title matches a launch/focus target string."""
    if not target:
        return False
    t = (title or "").strip()
    want = target.strip()
    if not t:
        return False
    if t == want:
        return True
    return want.lower() in t.lower()


def expand_win_path(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def resolve_browser_executable(browser: str) -> Path | None:
    """Resolve Edge/Chrome to an absolute path (PATH + common install dirs)."""
    name = {
        "edge": "msedge.exe",
        "msedge": "msedge.exe",
        "chrome": "chrome.exe",
        "msedge.exe": "msedge.exe",
        "chrome.exe": "chrome.exe",
    }.get(browser.lower(), browser)
    if not name.lower().endswith(".exe"):
        name = f"{name}.exe"

    which = shutil.which(name)
    if which:
        return Path(which)

    for candidate in _BROWSER_CANDIDATES.get(name.lower(), ()):
        p = Path(expand_win_path(candidate))
        if p.is_file():
            return p
    # Absolute path already?
    direct = Path(expand_win_path(browser))
    if direct.is_file():
        return direct
    return None


def is_calculator_app(app: str) -> bool:
    a = (app or "").strip().lower()
    return a in {
        "calc",
        "calc.exe",
        "calculator",
        "calculator:",
        "calculatorapp",
        "calculatorapp.exe",
        _CALC_AUMID.lower(),
    }


def calculator_launch_strategies() -> list[tuple[str, list[str] | None]]:
    """Ordered Win11 Calculator start strategies.

    Each entry is (label, argv_or_None). ``None`` argv means use ``os.startfile``.
    Avoid ``Application.start('calc.exe')`` — calc.exe is a stub that exits after
    launching the Store app, which makes pywinauto wait/timeout.
    """
    return [
        ("protocol:calculator:", None),  # os.startfile("calculator:")
        (
            "cmd:start calculator:",
            ["cmd.exe", "/c", "start", "", "calculator:"],
        ),
        (
            "explorer:AppsFolder",
            ["explorer.exe", f"shell:AppsFolder\\{_CALC_AUMID}"],
        ),
        ("calc.exe", ["calc.exe"]),
    ]


def file_uri_to_path(url: str) -> Path | None:
    """Best-effort file:// → Path (for existence checks)."""
    if not url.lower().startswith("file:"):
        p = Path(url)
        return p if p.exists() else None
    parsed = urlparse(url)
    path = unquote(parsed.path or "")
    # file:///C:/foo → /C:/foo on posix parsers; strip leading slash on Windows drives
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    elif path.startswith("/") and sys.platform == "win32":
        # UNC or odd forms
        pass
    p = Path(path)
    return p


class WindowsBackend:
    """pywinauto/UIA backend. Instantiation fails clearly off Windows."""

    name = "windows"

    def __init__(
        self,
        allow: Sequence[str] | None = None,
        *,
        launch_timeout_s: float | None = None,
        control_timeout_s: float | None = None,
    ) -> None:
        if not is_windows():
            err = windows_unavailable_error()
            raise RuntimeError(err.message)
        ensure_pywinauto()

        env_launch = os.environ.get("WINCUA_LAUNCH_TIMEOUT_S")
        env_control = os.environ.get("WINCUA_CONTROL_TIMEOUT_S")
        self.launch_timeout_s = float(
            launch_timeout_s
            if launch_timeout_s is not None
            else (env_launch or _DEFAULT_LAUNCH_TIMEOUT_S)
        )
        self.control_timeout_s = float(
            control_timeout_s
            if control_timeout_s is not None
            else (env_control or _DEFAULT_CONTROL_TIMEOUT_S)
        )

        self.allow: list[str] = [a.lower() for a in (allow or [])]
        self._elements: dict[str, ElementRef] = {}
        self._wrappers: dict[str, Any] = {}
        self._window: Any = None
        self._target_title: str | None = None
        self._id_seq = 0

        # Soften pywinauto global timings so missing windows fail with our errors
        try:
            from pywinauto import timings

            timings.Timings.window_find_timeout = min(5.0, self.control_timeout_s)
            timings.Timings.exists_timeout = min(3.0, self.control_timeout_s)
        except Exception:
            pass

    def set_allow(self, allow: Sequence[str]) -> None:
        self.allow = [a.lower() for a in allow]

    def _next_id(self, hint: str = "e") -> str:
        self._id_seq += 1
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", hint)[:40] or "e"
        return f"{safe}_{self._id_seq}"

    def _check_process_allowed(self, exe_name: str) -> StructuredError | None:
        if not self.allow:
            return StructuredError(
                code="allow_list_empty",
                message=(
                    "Allow-list is empty; refusing desktop action. "
                    "Pass allow=[...] on Agent/Session or WindowsBackend."
                ),
                details={"exe": exe_name},
            )
        base = Path(exe_name).name.lower()
        allowed = {Path(a).name.lower() for a in self.allow}
        if base in allowed or exe_name.lower() in allowed:
            return None
        # Protocol / AUMID launches for Calculator
        if is_calculator_app(exe_name) and any(
            a in allowed
            for a in (
                "calc.exe",
                "calculatorapp.exe",
                "applicationframehost.exe",
                "calculator:",
                "calculator",
            )
        ):
            return None
        return StructuredError(
            code="allow_list_denied",
            message=f"Process {base!r} is not on the session allow-list.",
            details={"exe": base, "allow": list(self.allow)},
        )

    def _desktop(self) -> Any:
        from pywinauto import Desktop

        return Desktop(backend="uia")

    def _visible_titles(self, limit: int = 25) -> list[str]:
        titles: list[str] = []
        try:
            for w in self._desktop().windows():
                try:
                    name = (w.window_text() or "").strip()
                except Exception:
                    try:
                        name = (getattr(w.element_info, "name", None) or "").strip()
                    except Exception:
                        name = ""
                if name:
                    titles.append(name)
                if len(titles) >= limit:
                    break
        except Exception as exc:
            titles.append(f"<enumerate failed: {exc}>")
        return titles

    def _find_window_once(self, target: str) -> Any | None:
        desktop = self._desktop()
        if target in ("", "foreground", "desktop"):
            try:
                wins = [w for w in desktop.windows() if self._window_is_usable(w)]
                return wins[0] if wins else None
            except Exception:
                return None

        # Enumerate — more reliable than WindowSpecification that always "exists"
        try:
            windows = desktop.windows()
        except Exception:
            windows = []

        for w in windows:
            try:
                name = w.window_text() or ""
            except Exception:
                try:
                    name = getattr(w.element_info, "name", "") or ""
                except Exception:
                    continue
            if match_window_title(name, target) and self._window_is_usable(w):
                return w
        return None

    def _window_is_usable(self, window: Any) -> bool:
        try:
            # Filter out invisible / zero-size shells when possible
            if hasattr(window, "is_visible") and not window.is_visible():
                return False
        except Exception:
            pass
        return True

    def _find_window(self, target: str, *, timeout_s: float = 0.0) -> Any | None:
        deadline = time.time() + max(0.0, timeout_s)
        while True:
            found = self._find_window_once(target)
            if found is not None:
                return found
            if time.time() >= deadline:
                return None
            time.sleep(0.25)

    def _wait_controls(
        self,
        window: Any,
        auto_ids: Sequence[str],
        *,
        timeout_s: float | None = None,
    ) -> str | None:
        """Wait until one of the AutomationIds is present; return the one found."""
        timeout = self.control_timeout_s if timeout_s is None else timeout_s
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            for auto_id in auto_ids:
                try:
                    ctrl = window.child_window(auto_id=auto_id, control_type="Button")
                    if ctrl.exists(timeout=0.2):
                        return auto_id
                except Exception as exc:
                    last_err = exc
                    try:
                        ctrl = window.child_window(auto_id=auto_id)
                        if ctrl.exists(timeout=0.2):
                            return auto_id
                    except Exception as exc2:
                        last_err = exc2
            time.sleep(0.2)
        if last_err:
            return None
        return None

    def _walk_uia(self, window: Any, limit: int = 120) -> list[ElementRef]:
        refs: list[ElementRef] = []
        self._elements.clear()
        self._wrappers.clear()

        def add(wrapper: Any) -> None:
            if len(refs) >= limit:
                return
            try:
                info = wrapper.element_info
                name = (getattr(info, "name", None) or "")[:200]
                role = (getattr(info, "control_type", None) or "")[:80]
                auto_id = (getattr(info, "automation_id", None) or "")[:120]
                rect = None
                try:
                    r = wrapper.rectangle()
                    rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
                except Exception:
                    rect = None
                patterns: list[str] = []
                try:
                    iface = getattr(wrapper, "iface_invoke", None)
                    if iface is not None:
                        patterns.append("Invoke")
                except Exception:
                    pass
                hint = auto_id or name or role or "el"
                eid = self._next_id(hint)
                ref = ElementRef(
                    element_id=eid,
                    role=str(role),
                    name=str(name),
                    bounds=rect,
                    patterns=tuple(patterns),
                )
                self._elements[eid] = ref
                self._wrappers[eid] = wrapper
                if auto_id:
                    self._wrappers[f"auto:{auto_id}"] = wrapper
                    self._elements[f"auto:{auto_id}"] = ref
                if name:
                    self._wrappers[f"name:{name}"] = wrapper
                refs.append(ref)
            except Exception:
                return

        add(window)
        try:
            descendants = window.descendants()
        except Exception:
            descendants = []
        for child in descendants:
            if len(refs) >= limit:
                break
            add(child)
        return refs

    def _resolve_wrapper(self, element_id: str | None) -> Any | None:
        if not element_id:
            return None
        if element_id in self._wrappers:
            w = self._wrappers[element_id]
            try:
                if hasattr(w, "exists") and not w.exists(timeout=0.5):
                    # Stale cache — fall through to live lookup
                    pass
                else:
                    return w
            except Exception:
                return w
        if self._window is not None:
            if element_id.startswith("auto:"):
                auto_id = element_id[5:]
                try:
                    ctrl = self._window.child_window(auto_id=auto_id)
                    if ctrl.exists(timeout=self.control_timeout_s):
                        self._wrappers[element_id] = ctrl
                        return ctrl
                except Exception:
                    return None
            if element_id.startswith("name:"):
                name = element_id[5:]
                try:
                    ctrl = self._window.child_window(title=name)
                    if ctrl.exists(timeout=self.control_timeout_s):
                        self._wrappers[element_id] = ctrl
                        return ctrl
                except Exception:
                    return None
        return None

    async def observe(self, target: str, include: Sequence[str]) -> Observation:
        return await asyncio.to_thread(self._observe_sync, target, list(include))

    def _observe_sync(self, target: str, include: list[str]) -> Observation:
        errors: list[StructuredError] = []
        uia: list[ElementRef] = []
        screenshot = None
        app: dict[str, Any] | None = None

        window = self._find_window(target, timeout_s=min(5.0, self.launch_timeout_s))
        if window is None:
            errors.append(
                StructuredError(
                    code="window_not_found",
                    message=(
                        f"No UIA window matched target={target!r}. "
                        f"Visible titles: {self._visible_titles()}"
                    ),
                    details={"target": target, "visible": self._visible_titles()},
                )
            )
            return Observation(errors=errors)

        self._window = window
        self._target_title = target

        if "uia" in include:
            uia = self._walk_uia(window)

        if "app" in include:
            display = None
            for key in ("CalculatorResults", "CalculatorExpression"):
                try:
                    w = window.child_window(auto_id=key)
                    if w.exists(timeout=0.5):
                        display = w.window_text()
                        break
                except Exception:
                    continue
            title = ""
            try:
                title = window.window_text() or ""
            except Exception:
                pass
            app = {
                "target": target,
                "title": title,
                "display": display,
            }

        if "screenshot" in include:
            try:
                img = window.capture_as_image()
                from io import BytesIO

                buf = BytesIO()
                img.save(buf, format="PNG")
                screenshot = ImageRef(data=buf.getvalue(), capture_quality="full")
            except Exception as exc:
                screenshot = ImageRef(capture_quality="failed")
                errors.append(
                    StructuredError(
                        code="screenshot_failed",
                        message=str(exc),
                    )
                )

        return Observation(uia=uia, screenshot=screenshot, app=app, errors=errors)

    async def act(self, action: Action, *, dry_run: bool = False) -> ActionResult:
        return await asyncio.to_thread(self._act_sync, action, dry_run)

    def _act_sync(self, action: Action, dry_run: bool) -> ActionResult:
        if dry_run:
            return ActionResult(ok=True, mode_used="dry-run")

        try:
            if isinstance(action, Semantic):
                return self._semantic(action)
            if isinstance(action, Invoke):
                return self._invoke(action.element_id)
            if isinstance(action, Click):
                return self._click(action)
            if isinstance(action, Type):
                return self._type(action)
            if isinstance(action, Key):
                return self._key(action.keys)
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(
                    code="unsupported_action",
                    message=f"Unsupported action type: {type(action).__name__}",
                ),
            )
        except Exception as exc:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(code="act_failed", message=str(exc)),
            )

    def _invoke(self, element_id: str) -> ActionResult:
        wrapper = self._resolve_wrapper(element_id)
        if wrapper is None:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(
                    code="element_not_found",
                    message=(
                        f"No element for id={element_id!r} in "
                        f"target={self._target_title!r}. "
                        "Is Calculator/browser focused and UIA ready?"
                    ),
                    details={"element_id": element_id, "visible": self._visible_titles()},
                ),
            )
        try:
            wrapper.invoke()
        except Exception:
            try:
                wrapper.click_input()
            except Exception as exc:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(code="invoke_failed", message=str(exc)),
                )
        return ActionResult(ok=True, mode_used="windows")

    def _click(self, action: Click) -> ActionResult:
        if action.element_id:
            wrapper = self._resolve_wrapper(action.element_id)
            if wrapper is None:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="element_not_found",
                        message=f"No element for id={action.element_id!r}",
                    ),
                )
            wrapper.click_input()
            return ActionResult(ok=True, mode_used="windows")
        if action.x is not None and action.y is not None:
            from pywinauto import mouse

            mouse.click(coords=(action.x, action.y))
            return ActionResult(ok=True, mode_used="windows")
        return ActionResult(
            ok=False,
            mode_used="windows",
            error=StructuredError(
                code="invalid_click",
                message="Click requires element_id or x/y",
            ),
        )

    def _type(self, action: Type) -> ActionResult:
        if action.element_id:
            wrapper = self._resolve_wrapper(action.element_id)
            if wrapper is None:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="element_not_found",
                        message=f"No element for id={action.element_id!r}",
                    ),
                )
            try:
                wrapper.set_focus()
            except Exception:
                pass
            try:
                wrapper.type_keys(action.text, with_spaces=True)
            except Exception:
                from pywinauto.keyboard import send_keys

                send_keys(action.text, with_spaces=True)
        else:
            from pywinauto.keyboard import send_keys

            send_keys(action.text, with_spaces=True)
        if action.submit:
            from pywinauto.keyboard import send_keys

            send_keys("{ENTER}")
        return ActionResult(ok=True, mode_used="windows")

    def _key(self, keys: str) -> ActionResult:
        from pywinauto.keyboard import send_keys

        mapped = keys
        if keys.upper() in {"F6"}:
            mapped = "{F6}"
        elif "+" in keys and not keys.startswith("{"):
            parts = [p.strip().lower() for p in keys.split("+")]
            mods = {"ctrl": "^", "alt": "%", "shift": "+"}
            prefix = "".join(mods[p] for p in parts[:-1] if p in mods)
            last = parts[-1]
            special = {
                "enter": "{ENTER}",
                "tab": "{TAB}",
                "esc": "{ESC}",
                "escape": "{ESC}",
                "backspace": "{BACKSPACE}",
                "f6": "{F6}",
            }
            mapped = prefix + special.get(last, last)
        elif keys.lower() == "tab":
            mapped = "{TAB}"
        elif keys.lower() == "enter":
            mapped = "{ENTER}"
        send_keys(mapped, with_spaces=True)
        return ActionResult(ok=True, mode_used="windows")

    def _semantic(self, action: Semantic) -> ActionResult:
        name = action.name
        args = action.args or {}

        if name in ("wait", "system.wait"):
            if "ms" in args and "seconds" not in args:
                seconds = float(args["ms"]) / 1000.0
            else:
                seconds = float(args.get("seconds", 1))
            time.sleep(seconds)
            return ActionResult(ok=True, mode_used="windows")

        if name in ("launch", "app.launch"):
            return self._launch(
                app=str(args.get("app") or args.get("exe") or ""),
                title=args.get("title"),
                args_list=args.get("args") or [],
                timeout_s=float(args["timeout_s"]) if args.get("timeout_s") is not None else None,
            )

        if name in ("focus", "app.focus"):
            title = str(args.get("title") or args.get("target") or "")
            timeout_s = float(args.get("timeout_s", self.launch_timeout_s))
            window = self._find_window(title, timeout_s=timeout_s)
            if window is None:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="window_not_found",
                        message=(
                            f"Cannot focus title={title!r} within {timeout_s}s. "
                            f"Visible titles: {self._visible_titles()}. "
                            "If the app is open under a localized name, pass that title."
                        ),
                        details={"title": title, "visible": self._visible_titles()},
                    ),
                )
            try:
                window.set_focus()
            except Exception as exc:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(code="focus_failed", message=str(exc)),
                )
            self._window = window
            self._target_title = title
            return ActionResult(ok=True, mode_used="windows")

        if name in ("open_url", "browser.open"):
            return self._open_url(
                url=str(args.get("url") or ""),
                browser=str(args.get("browser") or "msedge"),
                title=args.get("target") or args.get("title"),
                timeout_s=float(args["timeout_s"]) if args.get("timeout_s") is not None else None,
            )

        if name in ("uia.invoke", "invoke_by"):
            eid = args.get("element_id")
            if not eid and args.get("auto_id"):
                eid = f"auto:{args['auto_id']}"
            if not eid and args.get("name"):
                eid = f"name:{args['name']}"
            if not eid:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="invalid_args",
                        message="uia.invoke needs element_id, auto_id, or name",
                    ),
                )
            if args.get("target"):
                win = self._find_window(str(args["target"]), timeout_s=self.control_timeout_s)
                if win is not None:
                    self._window = win
                    self._target_title = str(args["target"])
            return self._invoke(str(eid))

        if name in ("uia.type", "type_by"):
            eid = args.get("element_id")
            if not eid and args.get("auto_id"):
                eid = f"auto:{args['auto_id']}"
            if not eid and args.get("name"):
                eid = f"name:{args['name']}"
            text = str(args.get("text") or "")
            return self._type(
                Type(
                    text=text,
                    submit=bool(args.get("submit", False)),
                    element_id=str(eid) if eid else None,
                )
            )

        return ActionResult(
            ok=False,
            mode_used="windows",
            error=StructuredError(
                code="unknown_semantic",
                message=f"Unknown semantic action: {name!r}",
            ),
        )

    def _shell_start(self, target: str) -> None:
        """Start via ShellExecute / startfile (does not wait on a stub process)."""
        if hasattr(os, "startfile"):
            os.startfile(target)  # type: ignore[attr-defined]
            return
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", target],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _popen(self, argv: list[str]) -> subprocess.Popen[Any]:
        return subprocess.Popen(
            argv,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _launch(
        self,
        app: str,
        title: str | None = None,
        args_list: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> ActionResult:
        if not app:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(code="invalid_args", message="launch requires app"),
            )
        timeout = self.launch_timeout_s if timeout_s is None else timeout_s
        title = title or ("Calculator" if is_calculator_app(app) else None)

        denied = self._check_process_allowed(app)
        if denied:
            return ActionResult(ok=False, mode_used="windows", error=denied)

        # Already running? Just focus.
        if title:
            existing = self._find_window(title, timeout_s=0.5)
            if existing is not None:
                self._window = existing
                self._target_title = title
                try:
                    existing.set_focus()
                except Exception:
                    pass
                if is_calculator_app(app):
                    ready = self._wait_controls(
                        existing,
                        ("clearButton", "num1Button", "CalculatorResults"),
                        timeout_s=min(timeout, 15.0),
                    )
                    if ready is None:
                        # Window present but not ready — fall through and relaunch
                        pass
                    else:
                        return ActionResult(ok=True, mode_used="windows")
                else:
                    return ActionResult(ok=True, mode_used="windows")

        attempts: list[str] = []
        last_exc: Exception | None = None

        if is_calculator_app(app):
            for label, argv in calculator_launch_strategies():
                try:
                    if argv is None:
                        self._shell_start("calculator:")
                    else:
                        self._popen(argv)
                    attempts.append(f"{label}:ok")
                    break
                except Exception as exc:
                    attempts.append(f"{label}:{exc}")
                    last_exc = exc
            else:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="launch_failed",
                        message=(
                            "Could not start Windows Calculator. Tried: "
                            + "; ".join(attempts)
                            + ". Open Calculator once from the Start menu, then re-run."
                        ),
                        details={"attempts": attempts, "error": str(last_exc)},
                    ),
                )
        else:
            cmd = [app, *(args_list or [])]
            try:
                self._popen(cmd)
                attempts.append(f"popen:{app}:ok")
            except FileNotFoundError as exc:
                # Fall back to shell start for PATH-less apps
                try:
                    self._shell_start(app)
                    attempts.append(f"startfile:{app}:ok")
                except Exception as exc2:
                    return ActionResult(
                        ok=False,
                        mode_used="windows",
                        error=StructuredError(
                            code="launch_failed",
                            message=(
                                f"Could not start {app!r}: {exc}; startfile also failed: {exc2}"
                            ),
                        ),
                    )
            except Exception as exc:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(code="launch_failed", message=str(exc)),
                )

        if not title:
            return ActionResult(ok=True, mode_used="windows")

        window = self._find_window(title, timeout_s=timeout)
        if window is None:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(
                    code="launch_timeout",
                    message=(
                        f"Started {app!r} but no window matching title={title!r} "
                        f"appeared within {timeout}s. "
                        f"Visible titles: {self._visible_titles()}. "
                        f"Launch attempts: {attempts}. "
                        "Tips: run on an interactive desktop (not a service session); "
                        "open Calculator manually once; set WINCUA_LAUNCH_TIMEOUT_S=60."
                    ),
                    details={
                        "app": app,
                        "title": title,
                        "timeout_s": timeout,
                        "attempts": attempts,
                        "visible": self._visible_titles(),
                    },
                ),
            )

        self._window = window
        self._target_title = title
        try:
            window.set_focus()
        except Exception:
            pass

        if is_calculator_app(app):
            ready = self._wait_controls(
                window,
                ("clearButton", "num1Button", "equalButton"),
                timeout_s=min(timeout, 20.0),
            )
            if ready is None:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="uia_not_ready",
                        message=(
                            f"Calculator window found but UIA controls not ready "
                            f"within timeout (wanted clearButton/num1Button). "
                            f"Title matched; visible={self._visible_titles()}"
                        ),
                        details={"title": title, "visible": self._visible_titles()},
                    ),
                )

        return ActionResult(ok=True, mode_used="windows")

    def _open_url(
        self,
        url: str,
        browser: str = "msedge",
        title: str | None = None,
        timeout_s: float | None = None,
    ) -> ActionResult:
        if not url:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(code="invalid_args", message="open_url requires url"),
            )
        timeout = self.launch_timeout_s if timeout_s is None else timeout_s

        # Normalize file paths to file:// and verify fixture exists
        if re.match(r"^[A-Za-z]:\\", url) or url.startswith("\\\\"):
            path = Path(url)
            if not path.is_file():
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="fixture_missing",
                        message=f"Form fixture not found: {path}",
                    ),
                )
            url = path.resolve().as_uri()
        elif not url.startswith("file:") and "://" not in url:
            path = Path(url)
            if path.exists():
                url = path.resolve().as_uri()
        elif url.startswith("file:"):
            path = file_uri_to_path(url)
            if path is not None and not path.is_file():
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="fixture_missing",
                        message=(
                            f"file:// fixture does not exist on disk: {path}. "
                            "Run demos from the repo checkout so "
                            "examples/fixtures/form.html is present."
                        ),
                        details={"url": url, "path": str(path)},
                    ),
                )

        preferred = {
            "edge": "msedge.exe",
            "msedge": "msedge.exe",
            "chrome": "chrome.exe",
            "msedge.exe": "msedge.exe",
            "chrome.exe": "chrome.exe",
        }.get(browser.lower(), browser)

        order = [preferred]
        alt = "chrome.exe" if "edge" in preferred.lower() else "msedge.exe"
        if alt not in order:
            order.append(alt)

        tried: list[str] = []
        started: Path | None = None
        for name in order:
            denied = self._check_process_allowed(name)
            if denied:
                tried.append(f"{name}:allow_denied")
                continue
            exe = resolve_browser_executable(name)
            if exe is None:
                tried.append(f"{name}:not_found")
                continue
            try:
                self._popen([str(exe), url])
                started = exe
                tried.append(f"{exe}:ok")
                break
            except Exception as exc:
                tried.append(f"{exe}:{exc}")

        if started is None:
            searched = []
            for name in order:
                searched.extend(_BROWSER_CANDIDATES.get(name.lower(), ()))
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(
                    code="browser_not_found",
                    message=(
                        "Could not start Edge or Chrome to open the form fixture. "
                        f"Tried: {tried}. "
                        "Install Microsoft Edge (or Google Chrome), ensure "
                        "msedge.exe/chrome.exe exists under Program Files, "
                        "or pass --browser chrome. "
                        f"Searched paths like: {[expand_win_path(p) for p in searched[:4]]}"
                    ),
                    details={"tried": tried, "url": url},
                ),
            )

        title_hints = [
            t
            for t in (
                title,
                "wincua form demo",
                "form.html",
                "form demo",
                "Edge" if "edge" in started.name.lower() else "Chrome",
            )
            if t
        ]

        window = None
        matched_hint = None
        deadline = time.time() + timeout
        while time.time() < deadline and window is None:
            for hint in title_hints:
                window = self._find_window_once(str(hint))
                if window is not None:
                    matched_hint = hint
                    break
            if window is None:
                time.sleep(0.35)

        if window is None:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(
                    code="launch_timeout",
                    message=(
                        f"Started {started.name} with {url!r} but no browser window "
                        f"matched titles {title_hints} within {timeout}s. "
                        f"Visible titles: {self._visible_titles()}. "
                        "If the tab title differs, pass target= to open_url, or "
                        "increase WINCUA_LAUNCH_TIMEOUT_S."
                    ),
                    details={
                        "browser": str(started),
                        "url": url,
                        "hints": title_hints,
                        "visible": self._visible_titles(),
                        "tried": tried,
                    },
                ),
            )

        self._window = window
        self._target_title = str(matched_hint or title_hints[0])
        try:
            window.set_focus()
        except Exception:
            pass
        return ActionResult(ok=True, mode_used="windows")
