"""Windows UIA backend via pywinauto (MIT). Host Win32 only."""

from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import unquote, urlparse

# Win11 Calculator: clear may be clearButton or clearEntryButton depending on mode.
_CALC_AUTO_ID_ALIASES: dict[str, tuple[str, ...]] = {
    "clearButton": ("clearButton", "clearEntryButton"),
    "clearEntryButton": ("clearEntryButton", "clearButton"),
}

# Name fallbacks when AutomationId search fails (localized EN UI).
_CALC_NAME_FALLBACKS: dict[str, tuple[str, ...]] = {
    "clearButton": ("Clear", "Clear entry", "Clear Entry"),
    "clearEntryButton": ("Clear entry", "Clear Entry", "Clear"),
    "num1Button": ("One",),
    "num2Button": ("Two",),
    "num5Button": ("Five",),
    "num7Button": ("Seven",),
    "plusButton": ("Plus",),
    "equalButton": ("Equals", "Equal"),
    "CalculatorResults": ("Display is", "Result"),
}

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


# Host UIA stack: CPython 3.11+ including 3.15 with pinned deps
# (comtypes>=1.5.0, pywin32>=312 which publishes cp315 win_amd64 wheels).
_SUPPORTED_WINDOWS_PYTHON = "CPython 3.11+ (including 3.15 with comtypes>=1.5.0 and pywin32>=312)"
_WINDOWS_PIP_PACKAGES = (
    '"pywinauto>=0.6.9" "Pillow>=10.0" "comtypes>=1.5.0" "pywin32>=312"'
)


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

    # Probe installed dep versions when available (helps diagnose stale pins).
    dep_lines: list[str] = []
    for mod_name, attr in (("comtypes", "__version__"), ("win32api", None), ("pywin32", None)):
        try:
            mod = __import__(mod_name)
            if attr:
                dep_lines.append(f"  {mod_name}={getattr(mod, attr, '?')!r}")
            else:
                dep_lines.append(f"  {mod_name}=importable ({getattr(mod, '__file__', '?')})")
        except Exception as dep_exc:
            dep_lines.append(f"  {mod_name}=IMPORT FAIL: {type(dep_exc).__name__}: {dep_exc!r}")

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
            f"  {quoted} -m pip install {_WINDOWS_PIP_PACKAGES}\n"
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
            "Inspect the exception chain and dependency probes below.\n"
            f"Supported host path: {_SUPPORTED_WINDOWS_PYTHON}.\n"
            "On Python 3.15 specifically:\n"
            "  - need comtypes>=1.5.0 (fixes 3.15 IntFlag / ctypes layout; <1.5.0 breaks)\n"
            "  - need pywin32>=312 (publishes cp315-win_amd64 wheels on PyPI)\n"
            "  - if comtypes cache is corrupt: "
            f"{quoted} -m comtypes.clear_cache\n"
            "Upgrade into THIS interpreter, then re-import:\n"
            f"  {quoted} -m pip install -U \"comtypes>=1.5.0\" \"pywin32>=312\" \"pywinauto>=0.6.9\"\n"
            f"  {quoted} -m pip install -e \"packages/wincua-driver[windows]\"\n"
            f"  {quoted} -c \"import pywinauto; print(pywinauto.__version__)\""
        )

    version_note = ""
    if sys.version_info >= (3, 15):
        version_note = (
            f"\nNote: CPython {major_minor} is supported when windows extras are "
            "pinned to comtypes>=1.5.0 and pywin32>=312.\n"
        )

    deps_block = "\n".join(dep_lines) if dep_lines else "  (none)"
    return (
        f"{headline}\n"
        f"{version_note}"
        f"  sys.executable = {exe}\n"
        f"  sys.version    = {py_ver}\n"
        f"  {spec_note}\n"
        f"Dependency probes:\n{deps_block}\n"
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


def _process_name_for_pid(pid: int) -> str:
    """Best-effort process image name for a pid (Windows)."""
    if not pid:
        return ""
    try:
        import psutil  # type: ignore

        return Path(psutil.Process(pid).name()).name
    except Exception:
        pass
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
        import win32process  # type: ignore

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        try:
            # GetModuleFileNameEx needs PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
            # on older Windows; fall back to QueryFullProcessImageName via ctypes.
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                int(handle), 0, buf, ctypes.byref(size)
            )
            if ok:
                return Path(buf.value).name
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        pass
    return f"pid:{pid}"


def describe_uia_element(wrapper: Any) -> dict[str, Any]:
    """Flat dict of useful UIA fields for dumps / errors."""
    info = getattr(wrapper, "element_info", None)
    name = ""
    control_type = ""
    auto_id = ""
    class_name = ""
    pid = 0
    try:
        name = (getattr(info, "name", None) or wrapper.window_text() or "")[:200]
    except Exception:
        pass
    try:
        control_type = str(getattr(info, "control_type", None) or "")[:80]
    except Exception:
        pass
    try:
        auto_id = str(getattr(info, "automation_id", None) or "")[:120]
    except Exception:
        pass
    try:
        class_name = str(getattr(info, "class_name", None) or "")[:120]
    except Exception:
        pass
    try:
        pid = int(getattr(info, "process_id", None) or wrapper.process_id())
    except Exception:
        pid = 0
    return {
        "control_type": control_type,
        "auto_id": auto_id,
        "name": name,
        "class_name": class_name,
        "pid": pid,
        "process": _process_name_for_pid(pid) if pid else "",
    }


def format_uia_tree(
    root: Any,
    *,
    max_depth: int = 6,
    max_nodes: int = 200,
) -> str:
    """Indented UIA tree dump (control_type, auto_id, name, class, process)."""
    lines: list[str] = []
    count = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal count
        if count >= max_nodes or depth > max_depth:
            return
        meta = describe_uia_element(node)
        pad = "  " * depth
        lines.append(
            f"{pad}- [{meta['control_type']}] auto_id={meta['auto_id']!r} "
            f"name={meta['name']!r} class={meta['class_name']!r} "
            f"proc={meta['process']!r} pid={meta['pid']}"
        )
        count += 1
        if depth >= max_depth:
            return
        try:
            children = node.children()
        except Exception:
            try:
                children = node.descendants(depth=1)
            except Exception:
                children = []
        for child in children:
            if count >= max_nodes:
                lines.append(f"{pad}  … truncated at {max_nodes} nodes")
                return
            walk(child, depth + 1)

    walk(root, 0)
    return "\n".join(lines) if lines else "<empty tree>"


def list_toplevel_by_process(process_name: str) -> list[tuple[Any, dict[str, Any]]]:
    """All top-level UIA windows owned by ``process_name`` (e.g. CalculatorApp.exe)."""
    from pywinauto import Desktop

    want = process_name.lower()
    out: list[tuple[Any, dict[str, Any]]] = []
    try:
        windows = Desktop(backend="uia").windows()
    except Exception:
        return out
    for w in windows:
        meta = describe_uia_element(w)
        if (meta.get("process") or "").lower() == want:
            out.append((w, meta))
    return out


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
        self._temp_profile_dirs: list[str] = []

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

    def _bind_window(self, window: Any, title: str | None = None) -> None:
        """Remember the UIA root and warm the wrapper cache via a deep walk."""
        self._window = window
        if title:
            self._target_title = title
        # Prefetch descendants so later auto:/name: lookups hit cache (UWP Calculator
        # keeps buttons under ApplicationFrameHost → CalculatorApp content).
        try:
            self._walk_uia(window, limit=250)
        except Exception:
            pass

    def _restore_and_focus(self, window: Any) -> None:
        """Restore a minimized/ghost UWP frame and bring it to the foreground."""
        try:
            if hasattr(window, "is_minimized") and window.is_minimized():
                window.restore()
        except Exception:
            pass
        try:
            window.restore()
        except Exception:
            pass
        try:
            window.set_focus()
        except Exception:
            pass
        # Win32 fallback — ApplicationFrameHost ghosts often need ShowWindow
        try:
            import win32con  # type: ignore
            import win32gui  # type: ignore

            hwnd = int(window.handle)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                # Alt-key trick sometimes needed when SetForegroundWindow is blocked
                import win32api  # type: ignore

                win32api.keybd_event(0x12, 0, 0, 0)  # ALT down
                win32api.keybd_event(0x12, 0, 2, 0)  # ALT up
                win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _window_key(self, window: Any) -> str:
        try:
            return f"hwnd:{int(window.handle)}"
        except Exception:
            meta = describe_uia_element(window)
            return f"{meta.get('pid')}:{meta.get('name')}:{id(window)}"

    def _calculator_roots(self, seed: Any | None = None) -> list[Any]:
        """Candidate UIA roots: title match, CalculatorApp.exe process, CoreWindow."""
        roots: list[Any] = []
        seen: set[str] = set()

        def add(w: Any | None) -> None:
            if w is None:
                return
            key = self._window_key(w)
            if key in seen:
                return
            seen.add(key)
            roots.append(w)

        add(seed)
        add(self._find_window_once("Calculator"))

        # Direct connect to CalculatorApp.exe (not just ApplicationFrameHost frame)
        try:
            from pywinauto import Application

            app = Application(backend="uia").connect(path="CalculatorApp.exe", timeout=3)
            try:
                for w in app.windows():
                    add(w)
            except Exception:
                pass
            try:
                add(app.top_window())
            except Exception:
                pass
        except Exception:
            pass

        for w, _meta in list_toplevel_by_process("CalculatorApp.exe"):
            add(w)
        for w, _meta in list_toplevel_by_process("ApplicationFrameHost.exe"):
            try:
                title = w.window_text() or ""
            except Exception:
                title = ""
            if match_window_title(title, "Calculator"):
                add(w)

        # UWP CoreWindow often hosts the real controls
        try:
            desktop = self._desktop()
            for w in desktop.windows():
                try:
                    cls = getattr(w.element_info, "class_name", "") or ""
                    title = w.window_text() or ""
                except Exception:
                    continue
                if "CoreWindow" in cls and match_window_title(title, "Calculator"):
                    add(w)
        except Exception:
            pass

        return roots

    def _find_by_name(
        self,
        root: Any,
        names: Sequence[str],
        *,
        control_type: str | None = "Button",
        prefix: bool = False,
    ) -> Any | None:
        for name in names:
            kwargs: dict[str, Any] = {"title": name}
            if control_type:
                kwargs["control_type"] = control_type
            try:
                ctrl = root.child_window(**kwargs)
                if ctrl.exists(timeout=0.3):
                    return ctrl
            except Exception:
                pass
            try:
                for d in root.descendants():
                    try:
                        dname = getattr(d.element_info, "name", None) or ""
                    except Exception:
                        continue
                    if prefix:
                        if dname.startswith(name):
                            return d
                    elif dname == name:
                        return d
            except Exception:
                continue
        return None

    def _deep_find_auto_id(
        self,
        root: Any,
        auto_id: str,
        *,
        timeout_s: float = 1.0,
    ) -> Any | None:
        """Find a control by AutomationId under a UWP/top-level root (deep search)."""
        deadline = time.time() + max(0.2, timeout_s)
        aliases = _CALC_AUTO_ID_ALIASES.get(auto_id, (auto_id,))
        while time.time() < deadline:
            for aid in aliases:
                # 1) child_window from root (searches descendants in UIA backend)
                try:
                    ctrl = root.child_window(auto_id=aid)
                    if ctrl.exists(timeout=0.4):
                        return ctrl
                except Exception:
                    pass
                try:
                    ctrl = root.child_window(auto_id=aid, control_type="Button")
                    if ctrl.exists(timeout=0.3):
                        return ctrl
                except Exception:
                    pass
                # 2) descendants(auto_id=...)
                try:
                    found = root.descendants(auto_id=aid)
                    if found:
                        return found[0]
                except Exception:
                    pass
                # 3) Manual walk of descendants (most reliable for UWP hosting)
                try:
                    for d in root.descendants():
                        try:
                            if (getattr(d.element_info, "automation_id", None) or "") == aid:
                                return d
                        except Exception:
                            continue
                except Exception:
                    pass
            # 4) Name fallbacks (EN Calculator)
            names = _CALC_NAME_FALLBACKS.get(auto_id, ())
            if names:
                prefix = auto_id == "CalculatorResults"
                ctrl = self._find_by_name(
                    root,
                    names,
                    control_type=None if prefix else "Button",
                    prefix=prefix,
                )
                if ctrl is not None:
                    return ctrl
            time.sleep(0.2)
        return None

    def _short_tree_dump(self, roots: Sequence[Any], *, max_depth: int = 4, max_nodes: int = 60) -> str:
        chunks: list[str] = []
        for i, root in enumerate(roots[:3]):
            meta = describe_uia_element(root)
            chunks.append(
                f"--- root[{i}] title={meta['name']!r} proc={meta['process']!r} "
                f"pid={meta['pid']} class={meta['class_name']!r}"
            )
            chunks.append(format_uia_tree(root, max_depth=max_depth, max_nodes=max_nodes))
        if not chunks:
            chunks.append("<no calculator roots>")
        return "\n".join(chunks)

    def _cache_name_controls(self, root: Any) -> bool:
        """Cache EN Calculator controls by Name; return True if digits + op/display ready."""
        found_digit = False
        for auto_id, names in _CALC_NAME_FALLBACKS.items():
            prefix = auto_id == "CalculatorResults"
            ctrl = self._find_by_name(
                root,
                names,
                control_type=None if prefix else "Button",
                prefix=prefix,
            )
            if ctrl is None:
                continue
            self._wrappers[f"auto:{auto_id}"] = ctrl
            for name in names:
                self._wrappers[f"name:{name}"] = ctrl
            if auto_id.startswith("num"):
                found_digit = True
        has_op = any(
            f"auto:{k}" in self._wrappers
            for k in ("plusButton", "equalButton", "clearButton", "clearEntryButton")
        )
        has_display = "auto:CalculatorResults" in self._wrappers
        return found_digit and (has_op or has_display)

    def _calc_ready_by_names(self, root: Any) -> bool:
        """True if classic EN Calculator chrome is visible by Name (no AutomationIds)."""
        return self._cache_name_controls(root)

    def _wait_controls(
        self,
        window: Any,
        auto_ids: Sequence[str],
        *,
        timeout_s: float | None = None,
    ) -> str | None:
        """Wait until one of the AutomationIds (or name fallbacks) is present."""
        timeout = self.control_timeout_s if timeout_s is None else timeout_s
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.3, deadline - time.time())
            per = min(1.5, remaining)
            for auto_id in auto_ids:
                ctrl = self._deep_find_auto_id(window, auto_id, timeout_s=per)
                if ctrl is not None:
                    self._wrappers[f"auto:{auto_id}"] = ctrl
                    try:
                        real = getattr(ctrl.element_info, "automation_id", None)
                        if real:
                            self._wrappers[f"auto:{real}"] = ctrl
                    except Exception:
                        pass
                    # Cache EN names too
                    for name in _CALC_NAME_FALLBACKS.get(auto_id, ()):
                        self._wrappers[f"name:{name}"] = ctrl
                    return auto_id
            if self._calc_ready_by_names(window):
                return "name:One"
            time.sleep(0.15)
        return None

    def _prepare_calculator(self, *, timeout_s: float, seed: Any | None = None) -> tuple[Any | None, str | None, str]:
        """Find a live Calculator UIA root with controls; return (root, ready_id, tree_dump)."""
        roots = self._calculator_roots(seed)
        tree = ""
        deadline = time.time() + timeout_s
        wanted = (
            "num1Button",
            "clearButton",
            "clearEntryButton",
            "equalButton",
            "CalculatorResults",
        )
        while time.time() < deadline:
            roots = self._calculator_roots(seed)
            for root in roots:
                self._restore_and_focus(root)
                ready = self._wait_controls(
                    root, wanted, timeout_s=min(2.5, max(0.5, deadline - time.time()))
                )
                if ready is not None:
                    self._bind_window(root, title="Calculator")
                    return root, ready, self._short_tree_dump([root])
            time.sleep(0.3)
        tree = self._short_tree_dump(roots)
        return None, None, tree

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

    def _search_roots(self) -> list[Any]:
        """Windows to search for controls — prefer bound root, then Calculator roots."""
        roots: list[Any] = []
        seen: set[str] = set()

        def add(w: Any | None) -> None:
            if w is None:
                return
            key = self._window_key(w)
            if key in seen:
                return
            seen.add(key)
            roots.append(w)

        add(self._window)
        title = (self._target_title or "").lower()
        if "calculator" in title or not roots:
            for w in self._calculator_roots(self._window):
                add(w)
        return roots

    def _resolve_wrapper(self, element_id: str | None) -> Any | None:
        if not element_id:
            return None
        if element_id in self._wrappers:
            w = self._wrappers[element_id]
            try:
                if hasattr(w, "exists") and not w.exists(timeout=0.5):
                    pass
                else:
                    return w
            except Exception:
                return w
        roots = self._search_roots()
        if not roots:
            return None
        if element_id.startswith("auto:"):
            auto_id = element_id[5:]
            for root in roots:
                ctrl = self._deep_find_auto_id(
                    root, auto_id, timeout_s=min(2.0, self.control_timeout_s)
                )
                if ctrl is not None:
                    self._wrappers[element_id] = ctrl
                    self._wrappers[f"auto:{auto_id}"] = ctrl
                    for name in _CALC_NAME_FALLBACKS.get(auto_id, ()):
                        self._wrappers[f"name:{name}"] = ctrl
                    if self._window is None:
                        self._bind_window(root)
                    return ctrl
            return None
        if element_id.startswith("name:"):
            name = element_id[5:]
            prefix = name == "Display is" or name.startswith("Display is")
            for root in roots:
                # Exact title / name, then contains (aria-label often duplicates)
                for kwargs in (
                    {"title": name},
                    {"title": name, "control_type": "Edit"},
                    {"title": name, "control_type": "Button"},
                    {"title_re": f".*{re.escape(name)}.*"},
                ):
                    try:
                        ctrl = root.child_window(**kwargs)
                        if ctrl.exists(timeout=min(2.0, self.control_timeout_s)):
                            self._wrappers[element_id] = ctrl
                            return ctrl
                    except Exception:
                        continue
                ctrl = self._find_by_name(
                    root,
                    (name,),
                    control_type=None,
                    prefix=prefix,
                )
                if ctrl is not None:
                    self._wrappers[element_id] = ctrl
                    return ctrl
                try:
                    for d in root.descendants():
                        try:
                            dname = getattr(d.element_info, "name", None) or ""
                            if dname == name or name.lower() in dname.lower():
                                self._wrappers[element_id] = d
                                return d
                        except Exception:
                            continue
                except Exception:
                    continue
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
        is_calc = match_window_title(target, "Calculator") or target.lower() == "calculator"
        if window is None and is_calc:
            # Title miss — still try CalculatorApp.exe process windows
            roots = self._calculator_roots(None)
            window = roots[0] if roots else None
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

        if is_calc:
            root, _ready, _tree = self._prepare_calculator(timeout_s=4.0, seed=window)
            if root is not None:
                window = root
            else:
                self._restore_and_focus(window)
                self._bind_window(window, title=target)
        else:
            self._bind_window(window, title=target)

        if "uia" in include:
            seen: set[str] = set()
            for key, ref in self._elements.items():
                if key.startswith("auto:") or key.startswith("name:"):
                    continue
                if ref.element_id in seen:
                    continue
                seen.add(ref.element_id)
                uia.append(ref)
            if not uia:
                uia = self._walk_uia(window)

        if "app" in include:
            display = None
            for root in self._search_roots():
                for key in ("CalculatorResults", "CalculatorExpression"):
                    w = self._deep_find_auto_id(root, key, timeout_s=1.0)
                    if w is not None:
                        try:
                            display = w.window_text()
                            break
                        except Exception:
                            continue
                if display:
                    break
                w = self._find_by_name(
                    root, ("Display is",), control_type=None, prefix=True
                )
                if w is not None:
                    try:
                        display = w.window_text()
                        break
                    except Exception:
                        pass
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
        # Calculator clear fallback: Escape when neither clear button exists
        if wrapper is None and element_id in ("auto:clearButton", "auto:clearEntryButton"):
            try:
                from pywinauto.keyboard import send_keys

                if self._window is not None:
                    try:
                        self._window.set_focus()
                    except Exception:
                        pass
                send_keys("{ESC}")
                return ActionResult(
                    ok=True,
                    mode_used="windows",
                    info={"fallback": "Escape", "wanted": element_id},
                )
            except Exception as exc:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="element_not_found",
                        message=(
                            f"No clear control for {element_id!r} and Escape fallback failed: {exc}"
                        ),
                    ),
                )
        if wrapper is None:
            tree = self._short_tree_dump(self._search_roots())
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(
                    code="element_not_found",
                    message=(
                        f"No element for id={element_id!r} in "
                        f"target={self._target_title!r}. "
                        "Tried ApplicationFrameHost + CalculatorApp.exe roots and "
                        "EN name fallbacks. Is Calculator focused and UIA ready?\n"
                        f"UIA tree dump:\n{tree}"
                    ),
                    details={
                        "element_id": element_id,
                        "visible": self._visible_titles(),
                        "tree": tree,
                    },
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

    def _read_value(self, wrapper: Any) -> str | None:
        for getter in (
            lambda w: w.get_value(),
            lambda w: w.iface_value.CurrentValue,
            lambda w: w.window_text(),
            lambda w: getattr(w.element_info, "name", None),
        ):
            try:
                val = getter(wrapper)
                if val is not None:
                    return str(val)
            except Exception:
                continue
        return None

    def _set_value(self, wrapper: Any, text: str) -> str:
        """Set an Edit/value control; return method used."""
        try:
            wrapper.set_edit_text(text)
            return "set_edit_text"
        except Exception:
            pass
        try:
            wrapper.iface_value.SetValue(text)
            return "ValuePattern"
        except Exception:
            pass
        try:
            wrapper.set_focus()
        except Exception:
            pass
        try:
            # Select-all then type for stubborn edits
            from pywinauto.keyboard import send_keys

            wrapper.type_keys("^a{BACKSPACE}" + text, with_spaces=True)
            return "type_keys"
        except Exception:
            from pywinauto.keyboard import send_keys

            send_keys("^a{BACKSPACE}" + text, with_spaces=True)
            return "send_keys"

    def _type(self, action: Type) -> ActionResult:
        method = "send_keys"
        verified: str | None = None
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
            method = self._set_value(wrapper, action.text)
            # Cheap verify — fail closed if the field did not take the value
            time.sleep(0.05)
            verified = self._read_value(wrapper)
            if verified is not None and action.text not in verified and verified != action.text:
                # Some edits report name/aria instead of value; retry type_keys once
                try:
                    from pywinauto.keyboard import send_keys

                    wrapper.set_focus()
                    send_keys("^a{BACKSPACE}" + action.text, with_spaces=True)
                    time.sleep(0.05)
                    verified = self._read_value(wrapper)
                    method = "type_keys_retry"
                except Exception:
                    pass
            if verified is not None and action.text not in verified and verified.strip() != action.text:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="type_verify_failed",
                        message=(
                            f"Typed {action.text!r} into {action.element_id!r} via {method}, "
                            f"but field value is {verified!r}"
                        ),
                        details={
                            "wanted": action.text,
                            "got": verified,
                            "element_id": action.element_id,
                            "method": method,
                        },
                    ),
                )
        else:
            from pywinauto.keyboard import send_keys

            send_keys(action.text, with_spaces=True)
        if action.submit:
            from pywinauto.keyboard import send_keys

            send_keys("{ENTER}")
        return ActionResult(
            ok=True,
            mode_used="windows",
            info={"method": method, "verified": verified, "text": action.text},
        )

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
                self._restore_and_focus(window)
            except Exception as exc:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(code="focus_failed", message=str(exc)),
                )
            # For Calculator, try process roots + name fallbacks before giving up
            if match_window_title(title, "Calculator") or title.lower() == "calculator":
                root, ready, tree = self._prepare_calculator(
                    timeout_s=min(self.control_timeout_s, 12.0),
                    seed=window,
                )
                if ready is None or root is None:
                    return ActionResult(
                        ok=False,
                        mode_used="windows",
                        error=StructuredError(
                            code="uia_not_ready",
                            message=(
                                "Focused Calculator but deep UIA search found no "
                                "num1Button/clearButton/CalculatorResults (also tried "
                                "CalculatorApp.exe roots and EN name fallbacks "
                                "One/Plus/Equals/Clear/Display is). "
                                f"Visible titles: {self._visible_titles()}\n"
                                f"UIA tree dump:\n{tree}"
                            ),
                            details={
                                "title": title,
                                "visible": self._visible_titles(),
                                "tree": tree,
                            },
                        ),
                    )
                return ActionResult(ok=True, mode_used="windows")
            self._bind_window(window, title=title)
            return ActionResult(ok=True, mode_used="windows")

        if name in ("open_url", "browser.open"):
            return self._open_url(
                url=str(args.get("url") or ""),
                browser=str(args.get("browser") or "msedge"),
                title=args.get("target") or args.get("title"),
                timeout_s=float(args["timeout_s"]) if args.get("timeout_s") is not None else None,
                new_window=bool(args.get("new_window", True)),
                isolated_profile=bool(args.get("isolated_profile", True)),
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
                    self._bind_window(win, title=str(args["target"]))
            return self._invoke(str(eid))

        if name in ("uia.type", "type_by"):
            eid = args.get("element_id")
            if not eid and args.get("auto_id"):
                eid = f"auto:{args['auto_id']}"
            if not eid and args.get("name"):
                eid = f"name:{args['name']}"
            if args.get("target"):
                win = self._find_window(str(args["target"]), timeout_s=self.control_timeout_s)
                if win is not None:
                    self._bind_window(win, title=str(args["target"]))
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

        # Already running? Just focus (Calculator: process roots + restore).
        if title:
            existing = self._find_window(title, timeout_s=0.5)
            if existing is not None:
                if is_calculator_app(app):
                    root, ready, _tree = self._prepare_calculator(
                        timeout_s=min(timeout, 15.0),
                        seed=existing,
                    )
                    if ready is not None and root is not None:
                        return ActionResult(ok=True, mode_used="windows")
                    # Window present but not ready — fall through and relaunch
                else:
                    self._window = existing
                    self._target_title = title
                    try:
                        self._restore_and_focus(existing)
                    except Exception:
                        pass
                    self._bind_window(existing, title=title)
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

        if is_calculator_app(app):
            root, ready, tree = self._prepare_calculator(
                timeout_s=min(timeout, 25.0),
                seed=window,
            )
            if ready is None or root is None:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="uia_not_ready",
                        message=(
                            "Calculator window found but deep UIA search found no "
                            "num1Button/clearButton/CalculatorResults (also tried "
                            "CalculatorApp.exe roots, restore/focus, and EN name "
                            "fallbacks One/Plus/Equals/Clear/Display is) within timeout. "
                            f"Visible titles: {self._visible_titles()}\n"
                            f"UIA tree dump:\n{tree}"
                        ),
                        details={
                            "title": title,
                            "visible": self._visible_titles(),
                            "tree": tree,
                            "attempts": attempts,
                        },
                    ),
                )
            return ActionResult(ok=True, mode_used="windows")

        self._bind_window(window, title=title)
        try:
            self._restore_and_focus(window)
        except Exception:
            pass

        return ActionResult(ok=True, mode_used="windows")

    def _open_url(
        self,
        url: str,
        browser: str = "msedge",
        title: str | None = None,
        timeout_s: float | None = None,
        *,
        new_window: bool = True,
        isolated_profile: bool = True,
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

        profile_dir: str | None = None
        if isolated_profile:
            profile_dir = tempfile.mkdtemp(prefix="wincua-browser-")
            self._temp_profile_dirs.append(profile_dir)

        tried: list[str] = []
        started: Path | None = None
        launch_argv: list[str] = []
        for name in order:
            denied = self._check_process_allowed(name)
            if denied:
                tried.append(f"{name}:allow_denied")
                continue
            exe = resolve_browser_executable(name)
            if exe is None:
                tried.append(f"{name}:not_found")
                continue
            argv = [str(exe)]
            # Fresh window + throwaway profile avoids reused tabs / first-run infobars
            # stealing keyboard focus from the form document.
            if new_window:
                argv.append("--new-window")
            if profile_dir:
                argv.append(f"--user-data-dir={profile_dir}")
            argv.extend(
                [
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-features=TranslateUI",
                    url,
                ]
            )
            try:
                self._popen(argv)
                started = exe
                launch_argv = argv
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
                    # Prefer a window that does NOT say "and N more page" (multi-tab reuse)
                    try:
                        t = window.window_text() or ""
                    except Exception:
                        t = ""
                    if "more page" in t.lower() and profile_dir:
                        window = None
                        continue
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
                        f"Launch argv: {launch_argv}. "
                        "If the tab title differs, pass target= to open_url, or "
                        "increase WINCUA_LAUNCH_TIMEOUT_S."
                    ),
                    details={
                        "browser": str(started),
                        "url": url,
                        "hints": title_hints,
                        "visible": self._visible_titles(),
                        "tried": tried,
                        "profile_dir": profile_dir,
                    },
                ),
            )

        self._bind_window(window, title=str(matched_hint or title_hints[0]))
        try:
            window.set_focus()
        except Exception:
            pass
        return ActionResult(
            ok=True,
            mode_used="windows",
            info={
                "browser": str(started),
                "profile_dir": profile_dir,
                "new_window": new_window,
            },
        )
