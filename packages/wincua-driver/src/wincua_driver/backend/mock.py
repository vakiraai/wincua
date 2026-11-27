"""Mock backend — deterministic fake tree for unit tests."""

from __future__ import annotations

from typing import Any, Sequence

from wincua_driver.types import (
    Action,
    ActionResult,
    Click,
    ElementRef,
    ImageRef,
    Invoke,
    Key,
    Observation,
    Type,
)


class MockBackend:
    name = "mock"

    def __init__(self) -> None:
        self.actions: list[Action] = []
        self.allow: list[str] = []
        self.display: str = "0"
        self._expr: list[str] = []
        self._form_chunks: list[str] = []

    def set_allow(self, allow: Sequence[str]) -> None:
        self.allow = list(allow)

    async def list_windows(self) -> list[dict[str, Any]]:
        return [
            {
                "title": "Calculator",
                "process": "ApplicationFrameHost.exe",
                "pid": 1001,
                "handle": 0x1001,
                "class_name": "ApplicationFrameWindow",
                "foreground": True,
            },
            {
                "title": "wincua form demo - Microsoft Edge",
                "process": "msedge.exe",
                "pid": 1002,
                "handle": 0x1002,
                "class_name": "Chrome_WidgetWin_1",
                "foreground": False,
            },
            {
                "title": "Untitled - Notepad",
                "process": "notepad.exe",
                "pid": 1003,
                "handle": 0x1003,
                "class_name": "Notepad",
                "foreground": False,
            },
        ]

    async def observe(self, target: str, include: Sequence[str]) -> Observation:
        uia: list[ElementRef] = []
        screenshot = None
        app = None
        if "uia" in include:
            uia = [
                ElementRef(element_id="e1", role="Window", name=target or "Mock"),
                ElementRef(
                    element_id="auto:CalculatorResults",
                    role="Text",
                    name=f"Display is {self.display}",
                ),
                ElementRef(element_id="auto:clearButton", role="Button", name="Clear", patterns=("Invoke",)),
                ElementRef(
                    element_id="auto:clearEntryButton",
                    role="Button",
                    name="Clear entry",
                    patterns=("Invoke",),
                ),
                ElementRef(element_id="auto:num1Button", role="Button", name="One", patterns=("Invoke",)),
                ElementRef(element_id="auto:num7Button", role="Button", name="Seven", patterns=("Invoke",)),
                ElementRef(element_id="auto:num2Button", role="Button", name="Two", patterns=("Invoke",)),
                ElementRef(element_id="auto:num5Button", role="Button", name="Five", patterns=("Invoke",)),
                ElementRef(element_id="auto:plusButton", role="Button", name="Plus", patterns=("Invoke",)),
                ElementRef(element_id="auto:equalButton", role="Button", name="Equals", patterns=("Invoke",)),
                ElementRef(element_id="e2", role="Button", name="One", patterns=("Invoke",)),
                ElementRef(element_id="e3", role="Button", name="Equals", patterns=("Invoke",)),
                ElementRef(element_id="name:Full name", role="Edit", name="Full name"),
                ElementRef(element_id="name:Email", role="Edit", name="Email"),
                ElementRef(
                    element_id="name:Submit form",
                    role="Button",
                    name="Submit form",
                    patterns=("Invoke",),
                ),
            ]
            if self.display.startswith("OK:"):
                uia.append(ElementRef(element_id="status", role="status", name=self.display))
        if "screenshot" in include:
            screenshot = ImageRef(path=None, data=b"", capture_quality="full")
        if "app" in include:
            app = {
                "display": f"Display is {self.display}",
                "target": target,
                "title": target or "Mock",
            }
        return Observation(uia=uia, screenshot=screenshot, app=app)

    def _apply_calc_button(self, auto_id: str | None) -> None:
        if not auto_id:
            return
        if auto_id in {"clearButton", "clearEntryButton"}:
            self._expr.clear()
            self.display = "0"
            return
        digits = {
            "num0Button": "0",
            "num1Button": "1",
            "num2Button": "2",
            "num3Button": "3",
            "num4Button": "4",
            "num5Button": "5",
            "num6Button": "6",
            "num7Button": "7",
            "num8Button": "8",
            "num9Button": "9",
        }
        if auto_id in digits:
            self._expr.append(digits[auto_id])
            self.display = "".join(self._expr)
            return
        if auto_id == "plusButton":
            self._expr.append("+")
            return
        if auto_id == "equalButton":
            try:
                # Very small evaluator for demo script 17+25
                expr = "".join(self._expr)
                if re_match := __import__("re").fullmatch(r"(\d+)\+(\d+)", expr):
                    self.display = str(int(re_match.group(1)) + int(re_match.group(2)))
                else:
                    self.display = expr
            except Exception:
                self.display = "Error"
            self._expr.clear()

    async def act(self, action: Action, *, dry_run: bool = False) -> ActionResult:
        from wincua_driver.types import Semantic

        self.actions.append(action)
        mode = "dry-run" if dry_run else "mock"
        if dry_run:
            return ActionResult(ok=True, mode_used=mode)
        if isinstance(action, Semantic):
            if action.name in ("wait", "system.wait", "launch", "app.launch", "focus", "app.focus", "open_url", "browser.open"):
                return ActionResult(ok=True, mode_used=mode)
            if action.name in ("uia.invoke", "invoke_by"):
                self._apply_calc_button(action.args.get("auto_id"))
                if (action.args.get("name") or "") == "Submit form":
                    self._finish_form_mock()
                return ActionResult(ok=True, mode_used=mode)
            if action.name in ("uia.type", "type_by"):
                text = str(action.args.get("text") or "")
                if text:
                    self._form_chunks.append(text)
                    self.display = " ".join(self._form_chunks)
                if action.args.get("submit"):
                    self._finish_form_mock()
                return ActionResult(
                    ok=True,
                    mode_used=mode,
                    info={"method": "mock", "verified": text, "text": text},
                )
            return ActionResult(ok=True, mode_used=mode)
        if isinstance(action, Invoke):
            eid = action.element_id or ""
            if eid.startswith("auto:"):
                self._apply_calc_button(eid[5:])
            return ActionResult(ok=True, mode_used=mode)
        if isinstance(action, Type):
            if action.text:
                self._form_chunks.append(action.text)
                self.display = " ".join(self._form_chunks)
            if action.submit:
                self._finish_form_mock()
            return ActionResult(ok=True, mode_used=mode)
        if isinstance(action, Click):
            return ActionResult(ok=True, mode_used=mode)
        if isinstance(action, Key):
            keys = (action.keys or "").lower()
            if keys in {"enter", "{enter}"} or keys.endswith("enter"):
                self._finish_form_mock()
            return ActionResult(ok=True, mode_used=mode)
        return ActionResult(ok=True, mode_used=mode)

    def _finish_form_mock(self) -> None:
        if len(self._form_chunks) >= 2:
            name, email = self._form_chunks[0], self._form_chunks[1]
            self.display = f"OK: {name} <{email}>"
        elif self._form_chunks:
            self.display = f"OK: {self._form_chunks[0]}"
