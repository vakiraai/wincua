"""Windows UIA backend via pywinauto (MIT). Host Win32 only."""

from __future__ import annotations

import asyncio
import dataclasses
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
from wincua_driver.window_state import Need, Win32WindowProbe, WindowProbe, check_window

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


def match_window_title(title: str, target: str, *, exact: bool = False) -> bool:
    """True if a window title matches a launch/focus target string."""
    if not target:
        return False
    t = (title or "").strip()
    want = target.strip()
    if not t:
        return False
    if t == want:
        return True
    if exact:
        return t.lower() == want.lower()
    return want.lower() in t.lower()


def is_calculator_target(target: str) -> bool:
    """True when the caller is asking for the Win11 Calculator app."""
    t = (target or "").strip().lower()
    return t in {"calculator", "calc", "calc.exe", "calculatorapp.exe"}


def window_class_name(wrapper: Any) -> str:
    try:
        return str(getattr(wrapper.element_info, "class_name", None) or "")
    except Exception:
        return ""


def window_title_of(wrapper: Any) -> str:
    try:
        return (wrapper.window_text() or "").strip()
    except Exception:
        try:
            return str(getattr(wrapper.element_info, "name", None) or "").strip()
        except Exception:
            return ""


def is_calculator_frame(wrapper: Any) -> bool:
    """Win11 Store Calculator top-level: ApplicationFrameWindow titled exactly Calculator."""
    return (
        window_class_name(wrapper) == "ApplicationFrameWindow"
        and window_title_of(wrapper) == "Calculator"
    )


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


def _uia_direct_children(node: Any) -> list[Any]:
    """Immediate children — same path the dump walker uses (crosses UWP hosts)."""
    try:
        kids = node.children()
        if kids is not None:
            return list(kids)
    except Exception:
        pass
    try:
        kids = node.descendants(depth=1)
        if kids is not None:
            return list(kids)
    except Exception:
        pass
    return []


def iter_uia_tree(
    root: Any,
    *,
    max_depth: int | None = None,
    max_nodes: int | None = None,
    include_root: bool = True,
):
    """Depth-first walk via ``children()`` (no depth cap unless requested).

    Shared by the dump helper and Calculator control lookup so both see the same
    ApplicationFrameWindow → CoreWindow → NavView → LandmarkTarget tree.
    """
    count = 0

    def walk(node: Any, depth: int):
        nonlocal count
        if max_nodes is not None and count >= max_nodes:
            return
        if include_root or depth > 0:
            yield node, depth
            count += 1
        if max_depth is not None and depth >= max_depth:
            return
        if max_nodes is not None and count >= max_nodes:
            return
        for child in _uia_direct_children(node):
            if max_nodes is not None and count >= max_nodes:
                return
            yield from walk(child, depth + 1)

    if include_root:
        yield from walk(root, 0)
    else:
        for child in _uia_direct_children(root):
            yield from walk(child, 1)


def format_uia_tree(
    root: Any,
    *,
    max_depth: int = 6,
    max_nodes: int = 200,
    values: bool = False,
) -> str:
    """Indented UIA tree dump (control_type, auto_id, name, class, process).

    ``values=True`` also prints the ValuePattern value of Edit/Document nodes.
    """
    lines: list[str] = []
    for node, depth in iter_uia_tree(root, max_depth=max_depth, max_nodes=max_nodes):
        meta = describe_uia_element(node)
        pad = "  " * depth
        extra = ""
        if values and meta["control_type"] in ("Edit", "ComboBox", "Document"):
            val = uia_value(node)
            if val is not None:
                extra = f" value={val[:120]!r}"
        lines.append(
            f"{pad}- [{meta['control_type']}] auto_id={meta['auto_id']!r} "
            f"name={meta['name']!r} class={meta['class_name']!r} "
            f"proc={meta['process']!r} pid={meta['pid']}{extra}"
        )
    if max_nodes is not None and len(lines) >= max_nodes:
        lines.append(f"  … truncated at {max_nodes} nodes")
    return "\n".join(lines) if lines else "<empty tree>"


def find_uia_by_auto_id(
    root: Any,
    auto_id: str,
    *,
    control_type: str | None = None,
    aliases: Sequence[str] | None = None,
) -> Any | None:
    """Find a descendant by AutomationId using the shared children() walk (uncapped)."""
    wanted = tuple(aliases) if aliases else (auto_id,)
    # Prefer pywinauto child_window (full descendant search) when available
    for aid in wanted:
        kwargs: dict[str, Any] = {"auto_id": aid}
        if control_type:
            kwargs["control_type"] = control_type
        try:
            ctrl = root.child_window(**kwargs)
            if ctrl.exists(timeout=0.5):
                return ctrl
        except Exception:
            pass
        try:
            found = root.descendants(auto_id=aid)
            if found:
                return found[0]
        except Exception:
            pass
    # Same traversal as the dump — reliable across ApplicationFrameHost → CoreWindow
    for node, _depth in iter_uia_tree(root, max_depth=None, max_nodes=None):
        try:
            aid = getattr(node.element_info, "automation_id", None) or ""
        except Exception:
            continue
        if aid not in wanted:
            continue
        if control_type:
            try:
                ct = str(getattr(node.element_info, "control_type", None) or "")
            except Exception:
                ct = ""
            if ct and ct != control_type:
                continue
        return node
    return None


def find_uia_by_name(
    root: Any,
    names: Sequence[str],
    *,
    control_type: str | None = None,
    prefix: bool = False,
) -> Any | None:
    """Find a descendant by Name using child_window then the shared children() walk."""
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
    for node, _depth in iter_uia_tree(root, max_depth=None, max_nodes=None):
        try:
            dname = getattr(node.element_info, "name", None) or ""
        except Exception:
            continue
        if control_type:
            try:
                ct = str(getattr(node.element_info, "control_type", None) or "")
            except Exception:
                ct = ""
            if ct and ct != control_type:
                continue
        for name in names:
            if prefix:
                if dname.startswith(name):
                    return node
            elif dname == name:
                return node
    return None


def find_core_window(root: Any) -> Any | None:
    """UWP content host under ApplicationFrameWindow (CalculatorApp.exe)."""
    for node, _depth in iter_uia_tree(root, max_depth=8, max_nodes=80):
        cls = window_class_name(node)
        if cls == "Windows.UI.Core.CoreWindow" or cls.endswith(".CoreWindow"):
            return node
    return None


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


def pick_calculator_frame(windows: Sequence[Any]) -> Any | None:
    """Prefer ApplicationFrameWindow titled exactly Calculator; never a Chrome tab."""
    exact_afw: list[Any] = []
    exact_other: list[Any] = []
    for w in windows:
        title = window_title_of(w)
        if title != "Calculator":
            continue
        if window_class_name(w) == "ApplicationFrameWindow":
            exact_afw.append(w)
        else:
            exact_other.append(w)
    if exact_afw:
        return exact_afw[0]
    if exact_other:
        # Still better than a substring Chrome hit
        return exact_other[0]
    return None


# Edge / Chrome: the top-level frame is Chrome_WidgetWin_1. Page content is a
# Document element under the Chrome_RenderWidgetHostHWND pane ("Chrome Legacy
# Window"); everything else in the frame is browser chrome (tabs, toolbar and an
# "Address and search bar" Edit that must never receive form text).
BROWSER_RENDER_CLASS = "Chrome_RenderWidgetHostHWND"
EDIT_TYPES: tuple[str, ...] = ("Edit", "ComboBox")
INVOKE_TYPES: tuple[str, ...] = (
    "Button",
    "Hyperlink",
    "MenuItem",
    "CheckBox",
    "RadioButton",
    "ListItem",
    "TabItem",
    "SplitButton",
)


def is_browser_window(wrapper: Any) -> bool:
    return window_class_name(wrapper).startswith("Chrome_WidgetWin")


def uia_control_type(wrapper: Any) -> str:
    try:
        return str(getattr(wrapper.element_info, "control_type", None) or "")
    except Exception:
        return ""


def uia_name(wrapper: Any) -> str:
    try:
        return str(getattr(wrapper.element_info, "name", None) or "")
    except Exception:
        return ""


def uia_value(wrapper: Any) -> str | None:
    """ValuePattern value (Edit text, Document URL); None when the control has none."""
    for getter in (lambda w: w.get_value(), lambda w: w.iface_value.CurrentValue):
        try:
            val = getter(wrapper)
        except Exception:
            continue
        if val is not None:
            return str(val)
    return None


def _uia_parent(node: Any) -> Any | None:
    try:
        return node.parent()
    except Exception:
        return None


def ancestor_chain(node: Any, *, limit: int = 12) -> str:
    """``Window[Chrome_WidgetWin_1] > Pane[…] > Document`` (best effort, for dumps)."""
    parts: list[str] = []
    cur: Any | None = node
    while cur is not None and len(parts) < limit:
        cls = window_class_name(cur)
        parts.append(f"{uia_control_type(cur) or '?'}" + (f"[{cls}]" if cls else ""))
        cur = _uia_parent(cur)
    return " > ".join(reversed(parts))


def list_browser_documents(window: Any, *, max_nodes: int = 4000) -> list[tuple[Any, str]]:
    """``(Document, parent class)`` pairs in tree order, outermost first.

    Fast path is one native FindAll (``descendants(control_type="Document")``);
    the fallback walks ``children()`` without descending into Documents.
    """
    out: list[tuple[Any, str]] = []
    try:
        found = window.descendants(control_type="Document")
    except Exception:
        found = None
    if found:
        for doc in found:
            parent = _uia_parent(doc)
            out.append((doc, window_class_name(parent) if parent is not None else ""))
        return out
    stack: list[tuple[Any, str]] = [(child, window_class_name(window)) for child in reversed(_uia_direct_children(window))]
    seen = 0
    while stack and seen < max_nodes:
        node, parent_cls = stack.pop()
        seen += 1
        if uia_control_type(node) == "Document":
            out.append((node, parent_cls))
            continue
        cls = window_class_name(node)
        stack.extend((child, cls) for child in reversed(_uia_direct_children(node)))
    return out


def find_browser_document(window: Any, title_hint: str | None = None) -> Any | None:
    """The page's Document: named like ``title_hint`` and/or hosted by the render widget."""
    hint = (title_hint or "").strip().lower()
    best: Any | None = None
    best_score = -1
    for doc, parent_cls in list_browser_documents(window):
        score = 0
        if BROWSER_RENDER_CLASS in (parent_cls, window_class_name(doc)):
            score += 2
        name = uia_name(doc).lower()
        if hint and name and (hint in name or name in hint):
            score += 4
        if score > best_score:
            best, best_score = doc, score
    return best


def find_named_preferring(root: Any, name: str, prefer: Sequence[str]) -> Any | None:
    """Exact-Name match whose control type is first in ``prefer``.

    A form label ("Full name", Text) and its input ("Full name", Edit) share the
    Name; typing must land on the Edit and invoking on the Button.
    """
    matches: list[Any] = []
    try:
        matches = list(root.descendants(title=name))
    except Exception:
        matches = []
    if not matches:
        matches = [n for n, _d in iter_uia_tree(root, include_root=False) if uia_name(n) == name]
    for ct in prefer:
        for node in matches:
            if uia_control_type(node) == ct:
                return node
    return None


_SEND_KEYS_SPECIAL = re.compile(r"([+^%~(){}\[\]])")


def escape_send_keys(text: str) -> str:
    """Literal text for pywinauto send_keys/type_keys (``+^%~(){}[]`` are modifiers/groups)."""
    return _SEND_KEYS_SPECIAL.sub(r"{\1}", text)


# COM / UIA HRESULTs that usually clear up on retry: the element went stale
# (Calculator re-creates buttons) or the UIA provider was busy.
TRANSIENT_HRESULTS: dict[int, str] = {
    -2147220991: "UIA_E_ELEMENTNOTAVAILABLE",  # 0x80040201
    -2147220992: "UIA_E_ELEMENTNOTENABLED",  # 0x80040200
    -2147220990: "UIA_E_NOCLICKABLEPOINT",  # 0x80040202
    -2146233083: "UIA_E_TIMEOUT",  # 0x80131505
    -2147417848: "RPC_E_DISCONNECTED",  # 0x80010108
    -2147418111: "RPC_E_CALL_REJECTED",  # 0x80010001
    -2147417846: "RPC_E_SERVERCALL_RETRYLATER",  # 0x8001010A
}


def _hresult(exc: BaseException) -> int | None:
    code = getattr(exc, "hresult", None)
    if code is None and exc.args and isinstance(exc.args[0], int):
        code = exc.args[0]
    if not isinstance(code, int):
        return None
    return code - (1 << 32) if code > 0x7FFFFFFF else code


def transient_uia_error(exc: BaseException) -> str | None:
    """Name of a retryable COM/UIA error in ``exc``'s chain, else None."""
    for e in _exception_chain(exc):
        code = _hresult(e)
        if code in TRANSIENT_HRESULTS:
            return TRANSIENT_HRESULTS[code]
    return None


def describe_com_error(exc: BaseException) -> str:
    name = transient_uia_error(exc)
    code = _hresult(exc)
    if code is not None:
        return f"{name or 'COM error'} 0x{code & 0xFFFFFFFF:08X}"
    return f"{type(exc).__name__}: {exc}"


def _candidate_ids(args: dict[str, Any]) -> list[str]:
    """Ids to try in order: element_id, then auto:<auto_id>, then name:<name>."""
    ids: list[str] = []
    if args.get("element_id"):
        ids.append(str(args["element_id"]))
    if args.get("auto_id"):
        ids.append(f"auto:{args['auto_id']}")
    if args.get("name"):
        ids.append(f"name:{args['name']}")
    return list(dict.fromkeys(ids))


class WindowsBackend:
    """pywinauto/UIA backend. Instantiation fails clearly off Windows."""

    name = "windows"
    invoke_retry_delay_s = 0.3
    focus_settle_s = 0.25
    # Before failing with window_minimized / target_obscured / …, restore and
    # focus the target once and check again. WINCUA_AUTO_FOCUS=0 turns it off.
    auto_focus = True
    _probe: WindowProbe | None = None
    _toplevel_hwnd: int | None = None

    def __init__(
        self,
        allow: Sequence[str] | None = None,
        *,
        launch_timeout_s: float | None = None,
        control_timeout_s: float | None = None,
        auto_focus: bool | None = None,
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
        if auto_focus is None:
            env_focus = os.environ.get("WINCUA_AUTO_FOCUS", "").strip().lower()
            auto_focus = env_focus not in ("0", "false", "no", "off")
        self.auto_focus = auto_focus

        self.allow: list[str] = [a.lower() for a in (allow or [])]
        self._elements: dict[str, ElementRef] = {}
        self._wrappers: dict[str, Any] = {}
        self._window: Any = None
        self._content_root: Any = None
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

    async def list_windows(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_windows_sync)

    def _list_windows_sync(self) -> list[dict[str, Any]]:
        """Visible titled top-level windows in desktop (z-) order."""
        foreground = 0
        try:
            import win32gui  # type: ignore

            foreground = int(win32gui.GetForegroundWindow())
        except Exception:
            pass
        out: list[dict[str, Any]] = []
        try:
            windows = list(self._desktop().windows())
        except Exception:
            return out
        for w in windows:
            if not self._window_is_usable(w):
                continue
            title = window_title_of(w)
            if not title:
                continue
            meta = describe_uia_element(w)
            try:
                handle = int(w.handle)
            except Exception:
                handle = 0
            out.append(
                {
                    "title": title,
                    "process": meta.get("process") or "",
                    "pid": meta.get("pid") or 0,
                    "handle": handle,
                    "class_name": meta.get("class_name") or "",
                    "foreground": bool(handle and handle == foreground),
                }
            )
        return out

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
            windows = list(desktop.windows())
        except Exception:
            windows = []

        usable = [w for w in windows if self._window_is_usable(w)]

        # Calculator: exact title + ApplicationFrameWindow (avoid Chrome PR tabs)
        if is_calculator_target(target) or (target or "").strip() == "Calculator":
            frame = pick_calculator_frame(usable)
            if frame is not None:
                return frame
            return None

        for w in usable:
            if match_window_title(window_title_of(w), target):
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
        self._toplevel_hwnd = None
        if title:
            self._target_title = title
        self._content_root = None
        root = window
        if is_browser_window(window):
            doc = find_browser_document(window, title or self._target_title)
            if doc is not None:
                self._content_root = root = doc
        # Prefetch via the shared children() walk (same as dump) so UWP Calculator
        # buttons under CoreWindow → NavView → LandmarkTarget are cached.
        try:
            self._walk_uia(root, limit=500)
        except Exception:
            pass

    def _browser_content(self) -> Any | None:
        """Page Document of the bound browser window (looked up again if it was not ready)."""
        doc = getattr(self, "_content_root", None)
        if doc is not None or self._window is None or not is_browser_window(self._window):
            return doc
        doc = find_browser_document(self._window, self._target_title)
        self._content_root = doc
        return doc

    def _is_browser_bound(self) -> bool:
        return self._window is not None and is_browser_window(self._window)

    def _bind_target(self, target: str) -> None:
        """Bind ``target`` unless it is already the bound window.

        Re-binding re-walks the tree, which renumbers every snapshot id
        (``num7Button_232``); ids from the last observation must keep working.
        """
        # Calculator binds its CoreWindow while _find_window returns the outer
        # ApplicationFrameWindow, so compare by bound target, not by window.
        if self._window is not None and self._target_title == target:
            try:
                alive = not hasattr(self._window, "exists") or self._window.exists(timeout=0)
            except Exception:
                alive = False
            if alive:
                return
        win = self._find_window(target, timeout_s=self.control_timeout_s)
        if win is None:
            return
        if self._window is not None and self._window_key(win) == self._window_key(self._window):
            return
        self._bind_window(win, title=target)

    def _restore_and_focus(self, window: Any) -> None:
        """Restore a minimized/ghost UWP frame and bring it to the foreground."""
        try:
            if hasattr(window, "is_minimized") and window.is_minimized():
                window.restore()
        except Exception:
            pass
        # SW_RESTORE would un-maximize a browser; it only needs the foreground.
        browser = is_browser_window(window)
        if not browser:
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
            if not browser:
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

    # --- window-state preflight -------------------------------------------

    def _window_probe(self) -> WindowProbe | None:
        probe = self._probe
        if probe is None and is_windows():
            try:
                probe = self._probe = Win32WindowProbe()
            except Exception:
                return None
        return probe

    def _target_hwnd(self, probe: WindowProbe) -> int:
        """Top-level HWND of the bound target (the ApplicationFrameWindow for Calculator)."""
        cached = self._toplevel_hwnd
        if cached and probe.is_window(cached):
            return cached
        try:
            hwnd = int(getattr(self._window, "handle", None) or 0)
        except Exception:
            hwnd = 0
        root = probe.root(hwnd) if hwnd else 0
        # A minimized UWP app's CoreWindow is detached from its frame.
        detached = bool(root) and probe.class_name(root).endswith("CoreWindow")
        if detached or (not root and is_calculator_target(self._target_title or "")):
            frame = self._calculator_frame(self._window)
            try:
                root = int(getattr(frame, "handle", None) or 0) if frame is not None else root
            except Exception:
                pass
        self._toplevel_hwnd = root or None
        return root

    def _window_state_error(self, need: Need, point: Any = None) -> StructuredError | None:
        """Check the bound window without trying to fix it (None when unbound or off Windows)."""
        probe = self._window_probe()
        if probe is None or self._window is None:
            return None
        hwnd = self._target_hwnd(probe)
        if not hwnd:
            return None
        pt = point() if callable(point) else point
        return check_window(probe, hwnd, need=need, point=pt)

    def _preflight(self, need: Need, point: Any = None, *, retry: bool | None = None) -> StructuredError | None:
        """Window-state check before acting; restore/focus once and re-check if allowed.

        ``point`` is an (x, y) or a callable returning one, re-read after the
        restore because a restored window has new element bounds.
        """
        err = self._window_state_error(need, point)
        if err is None:
            return None
        if not (self.auto_focus if retry is None else retry):
            return dataclasses.replace(err, details={**err.details, "auto_focus": False})
        probe = self._window_probe()
        hwnd = self._target_hwnd(probe) if probe is not None else 0
        try:
            if probe is not None and hwnd:
                probe.restore_and_focus(hwnd)
        except Exception:
            pass
        time.sleep(self.focus_settle_s)
        again = self._window_state_error(need, point)
        if again is None:
            self.__dict__.setdefault("_window_recovered", []).append(err.code)
            return None
        return dataclasses.replace(
            again,
            message=f"{again.message} Tried restore/focus once.",
            details={**again.details, "auto_focus": True, "first_error": err.code},
        )

    @staticmethod
    def _center(wrapper: Any) -> tuple[int, int] | None:
        try:
            r = wrapper.rectangle()
        except Exception:
            return None
        if r.right <= r.left or r.bottom <= r.top:
            return None
        return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)

    @staticmethod
    def _failed(err: StructuredError) -> ActionResult:
        return ActionResult(ok=False, mode_used="windows", error=err)

    def _window_key(self, window: Any) -> str:
        try:
            return f"hwnd:{int(window.handle)}"
        except Exception:
            meta = describe_uia_element(window)
            return f"{meta.get('pid')}:{meta.get('name')}:{id(window)}"

    def _calculator_frame(self, seed: Any | None = None) -> Any | None:
        """ApplicationFrameWindow titled exactly Calculator (not Chrome / not process connect)."""
        if seed is not None and is_calculator_frame(seed):
            return seed
        found = self._find_window_once("Calculator")
        if found is not None:
            return found
        # Desktop re-scan by class — CalculatorApp.exe has no top-level window of its own
        try:
            for w in self._desktop().windows():
                if self._window_is_usable(w) and is_calculator_frame(w):
                    return w
        except Exception:
            pass
        for w, _meta in list_toplevel_by_process("ApplicationFrameHost.exe"):
            if is_calculator_frame(w):
                return w
        return None

    def _calculator_roots(self, seed: Any | None = None) -> list[Any]:
        """Search roots: CoreWindow under ApplicationFrameWindow, then the frame itself."""
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

        frame = self._calculator_frame(seed)
        if frame is not None:
            core = find_core_window(frame)
            # Prefer CoreWindow — that is where num1Button / clearButton live
            add(core)
            add(frame)
        elif seed is not None and is_calculator_frame(seed):
            add(find_core_window(seed))
            add(seed)
        return roots

    def _find_by_name(
        self,
        root: Any,
        names: Sequence[str],
        *,
        control_type: str | None = "Button",
        prefix: bool = False,
    ) -> Any | None:
        return find_uia_by_name(
            root, names, control_type=control_type, prefix=prefix
        )

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
        # Buttons for keypad; display is not a Button
        prefer_button = auto_id not in ("CalculatorResults", "CalculatorExpression")
        while time.time() < deadline:
            ctrl = find_uia_by_auto_id(
                root,
                auto_id,
                control_type="Button" if prefer_button else None,
                aliases=aliases,
            )
            if ctrl is None and prefer_button:
                ctrl = find_uia_by_auto_id(root, auto_id, aliases=aliases)
            if ctrl is not None:
                return ctrl
            # Name fallbacks (EN Calculator)
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
            time.sleep(0.15)
        return None

    def _short_tree_dump(self, roots: Sequence[Any], *, max_depth: int = 6, max_nodes: int = 120) -> str:
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
            frame = self._calculator_frame(seed)
            focus_target = frame or (roots[0] if roots else seed)
            if focus_target is not None:
                self._restore_and_focus(focus_target)
            for root in roots:
                ready = self._wait_controls(
                    root, wanted, timeout_s=min(2.5, max(0.5, deadline - time.time()))
                )
                if ready is not None:
                    self._bind_window(root, title="Calculator")
                    if frame is not None:
                        try:
                            self._restore_and_focus(frame)
                        except Exception:
                            pass
                    return root, ready, self._short_tree_dump(
                        [r for r in (frame, root) if r is not None]
                    )
            time.sleep(0.3)
        dump_roots = roots or ([frame] if (frame := self._calculator_frame(seed)) else [])
        tree = self._short_tree_dump(dump_roots)
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
                    auto_id=str(auto_id),
                )
                self._elements[eid] = ref
                self._wrappers[eid] = wrapper
                if auto_id:
                    self._wrappers[f"auto:{auto_id}"] = wrapper
                    self._elements[f"auto:{auto_id}"] = ref
                name_key = f"name:{name}"
                if name and (
                    name_key not in self._wrappers or str(role) in EDIT_TYPES or str(role) in INVOKE_TYPES
                ):
                    self._wrappers[name_key] = wrapper
                refs.append(ref)
            except Exception:
                return

        for node, _depth in iter_uia_tree(window, max_depth=None, max_nodes=limit):
            add(node)
            if len(refs) >= limit:
                break
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

        add(self._browser_content())
        add(self._window)
        title = (self._target_title or "").lower()
        if "calculator" in title or not roots:
            for w in self._calculator_roots(self._window):
                add(w)
        return roots

    def _resolve_first(
        self, candidates: Sequence[str], *, prefer: Sequence[str] = ()
    ) -> str | None:
        """First candidate id (element_id, auto:…, name:…) that resolves; else the last one.

        The last candidate is returned unresolved so the caller's own lookup
        produces the usual element_not_found error.
        """
        ids = list(dict.fromkeys(c for c in candidates if c))
        for eid in ids[:-1]:
            if self._resolve_wrapper(eid, prefer=prefer) is not None:
                return eid
        return ids[-1] if ids else None

    def _resolve_wrapper(self, element_id: str | None, *, prefer: Sequence[str] = ()) -> Any | None:
        if not element_id:
            return None
        cached = self._wrappers.get(element_id)
        if cached is not None and prefer and element_id.startswith("name:") and uia_control_type(cached) not in prefer:
            for root in self._search_roots():
                better = find_named_preferring(root, element_id[5:], prefer)
                if better is not None:
                    self._wrappers[element_id] = better
                    cached = better
                    break
        if cached is not None:
            w = cached
            try:
                if not hasattr(w, "exists") or w.exists(timeout=0.5):
                    return w
            except Exception as exc:
                if not transient_uia_error(exc):
                    return w
            self._forget_wrapper(element_id)
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
            if prefer:
                for root in roots:
                    ctrl = find_named_preferring(root, name, prefer)
                    if ctrl is not None:
                        self._wrappers[element_id] = ctrl
                        return ctrl
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
        # Bare AutomationId ("clearButton") or a snapshot id from an older
        # observation ("clearButton_112"): fall back to the AutomationId search.
        base = re.sub(r"_\d+$", "", element_id)
        for candidate in dict.fromkeys((element_id, base)):
            if candidate and re.fullmatch(r"[A-Za-z][\w.]*", candidate):
                ctrl = self._resolve_wrapper(f"auto:{candidate}")
                if ctrl is not None:
                    self._wrappers[element_id] = ctrl
                    return ctrl
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

        if state := self._window_state_error("observe"):
            errors.append(state)

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

        if "app" in include and is_browser_window(window):
            app = self._browser_app_state(target, window)
        elif "app" in include:
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

    def _browser_app_state(self, target: str, window: Any) -> dict[str, Any]:
        """Title, page Document name/URL and current Edit values of a browser window."""
        app: dict[str, Any] = {"target": target, "title": window_title_of(window), "display": None}
        doc = self._browser_content()
        if doc is None:
            app["page"] = None
            app["note"] = (
                "page Document not found in the UIA tree yet (Chromium accessibility may still be "
                "starting); observe again"
            )
            return app
        app["page"] = uia_name(doc)
        url = uia_value(doc)
        if url:
            app["url"] = url
        fields: dict[str, str] = {}
        for eid, ref in self._elements.items():
            if eid.startswith(("auto:", "name:")) or ref.role not in EDIT_TYPES:
                continue
            value = uia_value(self._wrappers[eid]) if eid in self._wrappers else None
            fields[ref.name or ref.auto_id or eid] = value or ""
            if len(fields) >= 12:
                break
        if fields:
            app["fields"] = fields
        return app

    async def dump_tree(self, target: str, *, max_nodes: int = 400) -> str:
        """Plain-text UIA diagnostics for ``target`` (windows list, frame, page Document subtree)."""
        return await asyncio.to_thread(self._dump_tree_sync, target, max_nodes)

    def _dump_tree_sync(self, target: str, max_nodes: int) -> str:
        lines = [f"=== wincua UIA dump: target={target!r} ===", "--- visible top-level windows ---"]
        for w in self._list_windows_sync():
            lines.append(
                f"  {w['title']!r} proc={w['process']!r} class={w['class_name']!r}"
                + (" [foreground]" if w.get("foreground") else "")
            )
        window = self._find_window(target, timeout_s=2.0)
        if window is None:
            lines.append(f"(no window title contains {target!r})")
            return "\n".join(lines)
        meta = describe_uia_element(window)
        lines.append(
            f"--- matched window title={meta['name']!r} class={meta['class_name']!r} "
            f"proc={meta['process']!r} pid={meta['pid']} (frame, depth 4) ---"
        )
        lines.append(format_uia_tree(window, max_depth=4, max_nodes=80))
        if not is_browser_window(window):
            lines.append("--- full tree ---")
            lines.append(format_uia_tree(window, max_depth=30, max_nodes=max_nodes, values=True))
            return "\n".join(lines)
        docs = list_browser_documents(window)
        lines.append(
            f"--- Document elements: {len(docs)} (page content should sit under {BROWSER_RENDER_CLASS}) ---"
        )
        for i, (doc, parent_cls) in enumerate(docs[:6]):
            lines.append(
                f"  [{i}] name={uia_name(doc)!r} parent_class={parent_cls!r} url={uia_value(doc)!r}\n"
                f"       path: {ancestor_chain(doc)}"
            )
        chosen = find_browser_document(window, target)
        if chosen is None:
            lines.append(
                "(no Document found: Chromium accessibility is probably off. wincua launches Edge with "
                "--force-renderer-accessibility; if Edge was already running with this profile, close it)"
            )
            lines.append("--- full frame tree ---")
            lines.append(format_uia_tree(window, max_depth=30, max_nodes=max_nodes))
            return "\n".join(lines)
        lines.append(f"--- chosen page Document {uia_name(chosen)!r}: subtree with Edit values ---")
        lines.append(format_uia_tree(chosen, max_depth=30, max_nodes=max_nodes, values=True))
        return "\n".join(lines)

    async def act(self, action: Action, *, dry_run: bool = False) -> ActionResult:
        return await asyncio.to_thread(self._act_sync, action, dry_run)

    def _act_sync(self, action: Action, dry_run: bool) -> ActionResult:
        if dry_run:
            return ActionResult(ok=True, mode_used="dry-run")
        self._window_recovered: list[str] = []
        result = self._dispatch(action)
        if self._window_recovered:
            # Never a silent focus change: report what the preflight fixed.
            result.info = {**(result.info or {}), "window_recovered": list(self._window_recovered)}
        return result

    def _dispatch(self, action: Action) -> ActionResult:
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

    def _forget_wrapper(self, element_id: str) -> None:
        """Drop a (stale) cached wrapper and every alias pointing at it."""
        stale = self._wrappers.pop(element_id, None)
        if stale is not None:
            for key in [k for k, v in self._wrappers.items() if v is stale]:
                self._wrappers.pop(key, None)

    def _click_center(self, wrapper: Any) -> None:
        try:
            rect = wrapper.rectangle()
            x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
            if rect.right <= rect.left or rect.bottom <= rect.top:
                raise ValueError("empty rectangle")
        except Exception:
            wrapper.click_input()
            return
        from pywinauto import mouse

        mouse.click(coords=(x, y))

    def _not_found(self, element_id: str) -> ActionResult:
        tree = self._short_tree_dump(self._search_roots(), max_depth=25 if self._is_browser_bound() else 6)
        if self._is_browser_bound():
            where = (
                "Searched the page Document (under Chrome_RenderWidgetHostHWND), then the browser "
                "frame. Use an id from the latest observation (auto:<html id> or name:<label>)."
            )
        else:
            where = (
                "Tried ApplicationFrameHost + CalculatorApp.exe roots and EN name fallbacks. "
                "Is the window focused and UIA ready?"
            )
        return ActionResult(
            ok=False,
            mode_used="windows",
            error=StructuredError(
                code="element_not_found",
                message=(
                    f"No element for id={element_id!r} in target={self._target_title!r}. {where}\n"
                    f"UIA tree dump:\n{tree}"
                ),
                details={"element_id": element_id, "visible": self._visible_titles(), "tree": tree},
            ),
        )

    def _invoke(self, element_id: str, *, prefer: Sequence[str] = INVOKE_TYPES) -> ActionResult:
        if err := self._preflight("pattern"):
            return self._failed(err)
        try:
            wrapper = self._resolve_wrapper(element_id, prefer=prefer)
        except Exception as exc:
            if not transient_uia_error(exc):
                raise
            time.sleep(self.invoke_retry_delay_s)
            self._forget_wrapper(element_id)
            wrapper = self._resolve_wrapper(element_id, prefer=prefer)
        # Calculator clear fallback: Escape when neither clear button exists
        if wrapper is None and element_id in ("auto:clearButton", "auto:clearEntryButton"):
            if err := self._preflight("keyboard"):
                return self._failed(err)
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
            return self._not_found(element_id)
        return self._invoke_wrapper(element_id, wrapper)

    def _invoke_wrapper(self, element_id: str, wrapper: Any) -> ActionResult:
        """Invoke; on a transient COM/UIA error re-resolve and retry once; then click the center."""
        try:
            wrapper.invoke()
            return ActionResult(ok=True, mode_used="windows")
        except Exception as exc:
            error: BaseException = exc
        info: dict[str, Any] = {"invoke_error": describe_com_error(error)}
        if transient_uia_error(error):
            time.sleep(self.invoke_retry_delay_s)
            self._forget_wrapper(element_id)
            try:
                fresh = self._resolve_wrapper(element_id)
            except Exception:
                fresh = None
            if fresh is not None:
                wrapper = fresh
                try:
                    wrapper.invoke()
                    return ActionResult(ok=True, mode_used="windows", info={**info, "retried": True})
                except Exception as exc:
                    error = exc
                    info["retry_error"] = describe_com_error(exc)
        if err := self._preflight("pointer", lambda: self._center(wrapper)):
            return self._failed(
                dataclasses.replace(
                    err,
                    message=f"Invoke {element_id!r} failed ({describe_com_error(error)}); "
                    f"not falling back to a click: {err.message}",
                    details={**err.details, **info},
                )
            )
        try:
            self._click_center(wrapper)
        except Exception as exc:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(
                    code="invoke_failed",
                    message=(
                        f"Invoke {element_id!r} failed ({describe_com_error(error)}) "
                        f"and the click fallback failed ({describe_com_error(exc)})"
                    ),
                    details=info,
                ),
            )
        return ActionResult(ok=True, mode_used="windows", info={**info, "fallback": "click"})

    def _click(self, action: Click) -> ActionResult:
        if action.element_id:
            wrapper = self._resolve_wrapper(action.element_id, prefer=INVOKE_TYPES + EDIT_TYPES)
            if wrapper is None:
                return self._not_found(action.element_id)
            if err := self._preflight("pointer", lambda: self._center(wrapper)):
                return self._failed(dataclasses.replace(err, details={**err.details, "element_id": action.element_id}))
            wrapper.click_input()
            return ActionResult(ok=True, mode_used="windows")
        if action.x is not None and action.y is not None:
            if err := self._preflight("pointer", (action.x, action.y)):
                return self._failed(err)
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
            wrapper.type_keys("^a{BACKSPACE}" + escape_send_keys(text), with_spaces=True)
            return "type_keys"
        except Exception:
            from pywinauto.keyboard import send_keys

            send_keys("^a{BACKSPACE}" + escape_send_keys(text), with_spaces=True)
            return "send_keys"

    def _type(self, action: Type, *, prefer: Sequence[str] = EDIT_TYPES) -> ActionResult:
        if err := self._preflight("keyboard"):
            return self._failed(err)
        method = "send_keys"
        verified: str | None = None
        if action.element_id:
            wrapper = self._resolve_wrapper(action.element_id, prefer=prefer)
            if wrapper is None:
                return self._not_found(action.element_id)
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
                    send_keys("^a{BACKSPACE}" + escape_send_keys(action.text), with_spaces=True)
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

            send_keys(escape_send_keys(action.text), with_spaces=True)
        if action.submit:
            from pywinauto.keyboard import send_keys

            send_keys("{ENTER}")
        return ActionResult(
            ok=True,
            mode_used="windows",
            info={"method": method, "verified": verified, "text": action.text},
        )

    def _key(self, keys: str) -> ActionResult:
        if err := self._preflight("keyboard"):
            return self._failed(err)
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
                return self._focus_result()
            if self._window is not None and self._window_key(window) == self._window_key(self._window):
                # Already bound: re-walking would renumber the snapshot ids just observed.
                self._target_title = title or self._target_title
            else:
                self._bind_window(window, title=title)
            return self._focus_result()

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
            candidates = _candidate_ids(args)
            if not candidates:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="invalid_args",
                        message="uia.invoke needs element_id, auto_id, or name",
                    ),
                )
            if args.get("target"):
                self._bind_target(str(args["target"]))
            eid = self._resolve_first(candidates, prefer=INVOKE_TYPES)
            return self._invoke(str(eid))

        if name in ("uia.type", "type_by"):
            if args.get("target"):
                self._bind_target(str(args["target"]))
            eid = self._resolve_first(_candidate_ids(args), prefer=EDIT_TYPES)
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

    def _focus_result(self) -> ActionResult:
        """focus is the recovery step, so it fails when the window did not come to the front."""
        if err := self._preflight("keyboard", retry=True):
            return self._failed(err)
        return ActionResult(ok=True, mode_used="windows")

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
            return ActionResult(ok=True, mode_used="windows", info={"title": "Calculator"})

        self._bind_window(window, title=title)
        try:
            self._restore_and_focus(window)
        except Exception:
            pass

        return ActionResult(
            ok=True,
            mode_used="windows",
            info={"title": window_title_of(window) or title},
        )

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
                    # Build the page accessibility tree up front instead of lazily
                    # when Chromium notices a UIA client (empty Document otherwise).
                    "--force-renderer-accessibility",
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

        generic_hints = {"Edge", "Chrome"}
        window = None
        matched_hint = None
        started_at = time.time()
        deadline = started_at + timeout
        while time.time() < deadline and window is None:
            # A bare "Edge" title would also match the user's own browser windows;
            # only fall back to it late, if the page title never showed up.
            late = time.time() - started_at > timeout * 0.6
            for hint in title_hints:
                if hint in generic_hints and not late:
                    continue
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

        hint = str(matched_hint or title_hints[0])
        doc_ready = self._wait_browser_document(window, hint, timeout_s=min(15.0, max(3.0, deadline - time.time())))
        self._bind_window(window, title=hint)
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
                "title": window_title_of(window) or hint,
                "document": uia_name(self._content_root) if self._content_root is not None else None,
                "document_ready": doc_ready,
            },
        )

    def _wait_browser_document(self, window: Any, title_hint: str, *, timeout_s: float) -> bool:
        """Wait until the page Document exists and has content (Chromium builds it asynchronously)."""
        deadline = time.time() + timeout_s
        while True:
            doc = find_browser_document(window, title_hint)
            if doc is not None and _uia_direct_children(doc):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.3)
