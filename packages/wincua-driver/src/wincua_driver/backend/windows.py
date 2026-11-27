"""Windows UIA backend via pywinauto (MIT). Host Win32 only."""

from __future__ import annotations

import asyncio
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

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

_CALC_TITLE_RE = re.compile(r"Calculator", re.I)


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


class WindowsBackend:
    """pywinauto/UIA backend. Instantiation fails clearly off Windows."""

    name = "windows"

    def __init__(self, allow: Sequence[str] | None = None) -> None:
        if not is_windows():
            err = windows_unavailable_error()
            raise RuntimeError(err.message)
        try:
            import pywinauto  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "WindowsBackend requires pywinauto. "
                "Install with: pip install -e 'packages/wincua-driver[windows]'"
            ) from exc

        self.allow: list[str] = [a.lower() for a in (allow or [])]
        self._elements: dict[str, ElementRef] = {}
        self._wrappers: dict[str, Any] = {}
        self._window: Any = None
        self._target_title: str | None = None
        self._id_seq = 0

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
        # UWP Calculator often surfaces as ApplicationFrameHost.exe
        if base in allowed or exe_name.lower() in allowed:
            return None
        return StructuredError(
            code="allow_list_denied",
            message=f"Process {base!r} is not on the session allow-list.",
            details={"exe": base, "allow": list(self.allow)},
        )

    def _desktop(self) -> Any:
        from pywinauto import Desktop

        return Desktop(backend="uia")

    def _find_window(self, target: str) -> Any | None:
        desktop = self._desktop()
        if target in ("", "foreground", "desktop"):
            try:
                return desktop.window(active_only=True)
            except Exception:
                wins = desktop.windows()
                return wins[0] if wins else None

        # Exact title, then regex
        try:
            return desktop.window(title=target)
        except Exception:
            pass
        try:
            return desktop.window(title_re=f".*{re.escape(target)}.*")
        except Exception:
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
                    if wrapper.get_toggle_state:  # type: ignore[truthy-function]
                        pass
                except Exception:
                    pass
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
                # stash auto_id in a stable secondary key
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
            return self._wrappers[element_id]
        # live lookup by auto_id / name if window known
        if self._window is not None:
            if element_id.startswith("auto:"):
                auto_id = element_id[5:]
                try:
                    return self._window.child_window(auto_id=auto_id)
                except Exception:
                    return None
            if element_id.startswith("name:"):
                name = element_id[5:]
                try:
                    return self._window.child_window(title=name)
                except Exception:
                    return None
        return None

    def _exe_for_window(self, window: Any) -> str:
        try:
            return str(window.element_info.process_id and "")
        except Exception:
            pass
        try:
            # Best-effort: process name via pywinauto
            from pywinauto.process import process_from_pid

            pid = window.process_id()
            p = process_from_pid(pid)
            return getattr(p, "name", lambda: "")() or ""
        except Exception:
            return ""

    async def observe(self, target: str, include: Sequence[str]) -> Observation:
        return await asyncio.to_thread(self._observe_sync, target, list(include))

    def _observe_sync(self, target: str, include: list[str]) -> Observation:
        errors: list[StructuredError] = []
        uia: list[ElementRef] = []
        screenshot = None
        app: dict[str, Any] | None = None

        window = self._find_window(target)
        if window is None:
            errors.append(
                StructuredError(
                    code="window_not_found",
                    message=f"No UIA window matched target={target!r}",
                    details={"target": target},
                )
            )
            return Observation(errors=errors)

        self._window = window
        self._target_title = target

        if "uia" in include:
            uia = self._walk_uia(window)

        if "app" in include:
            display = None
            # Calculator display AutomationId (Win10/11 Store Calculator)
            for key in ("auto:CalculatorResults", "auto:CalculatorExpression"):
                w = self._wrappers.get(key)
                if w is None and self._window is not None:
                    try:
                        w = self._window.child_window(auto_id=key.split(":", 1)[1])
                    except Exception:
                        w = None
                if w is not None:
                    try:
                        display = w.window_text()
                        break
                    except Exception:
                        continue
            app = {
                "target": target,
                "title": getattr(window.element_info, "name", "") or "",
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
                    message=f"No element for id={element_id!r}",
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

        # Accept "ctrl+s" style → ^s ; leave {ENTER} etc. alone
        mapped = keys
        if "+" in keys and not keys.startswith("{"):
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
            }
            mapped = prefix + special.get(last, last)
        send_keys(mapped, with_spaces=True)
        return ActionResult(ok=True, mode_used="windows")

    def _semantic(self, action: Semantic) -> ActionResult:
        name = action.name
        args = action.args or {}

        if name in ("wait", "system.wait"):
            time.sleep(float(args.get("seconds", args.get("ms", 0) / 1000 if "ms" in args else 1)))
            return ActionResult(ok=True, mode_used="windows")

        if name in ("launch", "app.launch"):
            return self._launch(
                app=str(args.get("app") or args.get("exe") or ""),
                title=args.get("title"),
                args_list=args.get("args") or [],
            )

        if name in ("focus", "app.focus"):
            title = str(args.get("title") or args.get("target") or "")
            window = self._find_window(title)
            if window is None:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="window_not_found",
                        message=f"Cannot focus title={title!r}",
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
            return ActionResult(ok=True, mode_used="windows")

        if name in ("open_url", "browser.open"):
            return self._open_url(
                url=str(args.get("url") or ""),
                browser=str(args.get("browser") or "msedge"),
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
            # Ensure window context for auto/name lookup
            if self._window is None and args.get("target"):
                self._window = self._find_window(str(args["target"]))
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

    def _launch(
        self,
        app: str,
        title: str | None = None,
        args_list: list[str] | None = None,
    ) -> ActionResult:
        if not app:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(code="invalid_args", message="launch requires app"),
            )
        denied = self._check_process_allowed(app)
        # calc.exe launches CalculatorApp / ApplicationFrameHost — allow either
        if denied and app.lower() in ("calc.exe", "calc"):
            if any(
                Path(a).name.lower()
                in {"calculatorapp.exe", "applicationframehost.exe", "calc.exe"}
                for a in self.allow
            ):
                denied = None
        if denied:
            return ActionResult(ok=False, mode_used="windows", error=denied)

        cmd = [app, *(args_list or [])]
        try:
            from pywinauto import Application

            Application(backend="uia").start(" ".join(f'"{c}"' if " " in c else c for c in cmd))
        except Exception:
            subprocess.Popen(cmd, shell=False)

        if title:
            deadline = time.time() + 15
            window = None
            while time.time() < deadline:
                window = self._find_window(title)
                if window is not None:
                    break
                time.sleep(0.3)
            if window is None:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="launch_timeout",
                        message=f"Launched {app!r} but window {title!r} not found",
                    ),
                )
            self._window = window
            try:
                window.set_focus()
            except Exception:
                pass
        return ActionResult(ok=True, mode_used="windows")

    def _open_url(self, url: str, browser: str = "msedge") -> ActionResult:
        if not url:
            return ActionResult(
                ok=False,
                mode_used="windows",
                error=StructuredError(code="invalid_args", message="open_url requires url"),
            )
        # Normalize file paths to file://
        if re.match(r"^[A-Za-z]:\\", url) or url.startswith("\\\\"):
            url = Path(url).resolve().as_uri()
        elif url.startswith("file:") is False and "://" not in url:
            path = Path(url)
            if path.exists():
                url = path.resolve().as_uri()

        browser_exe = {
            "edge": "msedge.exe",
            "msedge": "msedge.exe",
            "chrome": "chrome.exe",
            "msedge.exe": "msedge.exe",
            "chrome.exe": "chrome.exe",
        }.get(browser.lower(), browser)
        denied = self._check_process_allowed(browser_exe)
        if denied:
            return ActionResult(ok=False, mode_used="windows", error=denied)

        # Prefer shell-open via known browser for file://
        try:
            subprocess.Popen([browser_exe, url], shell=False)
        except FileNotFoundError:
            # Fall back to the other browser
            alt = "chrome.exe" if "edge" in browser_exe else "msedge.exe"
            denied_alt = self._check_process_allowed(alt)
            if denied_alt:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(
                        code="browser_not_found",
                        message=f"Could not start {browser_exe}; alt also denied/missing",
                        details={"url": url},
                    ),
                )
            try:
                subprocess.Popen([alt, url], shell=False)
                browser_exe = alt
            except FileNotFoundError as exc:
                return ActionResult(
                    ok=False,
                    mode_used="windows",
                    error=StructuredError(code="browser_not_found", message=str(exc)),
                )

        # Focus browser window (best-effort)
        host = urlparse(url).path
        time.sleep(1.0)
        title_hint = "form" if "form" in url.lower() else Path(host).name
        window = self._find_window(title_hint) or self._find_window(
            "Edge" if "edge" in browser_exe else "Chrome"
        )
        if window is not None:
            self._window = window
            try:
                window.set_focus()
            except Exception:
                pass
        return ActionResult(ok=True, mode_used="windows")
