"""Mock backend — deterministic fake tree for unit tests.

One fake tree serves both demos: Win11 Calculator buttons (``auto:num1Button`` …)
and the browser form fixture (``examples/fixtures/form.html``), whose inputs are
exposed the way Edge does: HTML id as AutomationId, ``<label for>`` as Name.
"""

from __future__ import annotations

import re
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
    StructuredError,
    Type,
)
from wincua_driver.window_state import (
    TARGET_OBSCURED,
    TARGET_OFFSCREEN,
    WINDOW_CLOAKED,
    WINDOW_HIDDEN,
    WINDOW_MINIMIZED,
    WINDOW_NOT_FOREGROUND,
    window_error,
)

_WINDOW_LEVEL = frozenset({WINDOW_MINIMIZED, WINDOW_HIDDEN, WINDOW_CLOAKED, TARGET_OFFSCREEN})
_NEEDS: dict[str, frozenset[str]] = {
    "observe": _WINDOW_LEVEL,
    "pattern": _WINDOW_LEVEL,
    "keyboard": _WINDOW_LEVEL | {WINDOW_NOT_FOREGROUND},
    "pointer": _WINDOW_LEVEL | {WINDOW_NOT_FOREGROUND, TARGET_OBSCURED},
}

FORM_TITLE = "wincua form demo"
FORM_FIELDS: dict[str, str] = {"fullName": "Full name", "email": "Email"}
FORM_SUBMIT = ("submitButton", "Submit form")


class MockBackend:
    name = "mock"

    def __init__(self) -> None:
        self.actions: list[Action] = []
        self.allow: list[str] = []
        self.display: str = "0"
        self._expr: list[str] = []
        self.form: dict[str, str] = {key: "" for key in FORM_FIELDS}
        self.form_result: str = ""
        self.form_title: str = FORM_TITLE
        self._form_focus: str | None = None
        # Simulated window state (window_minimized, target_obscured, …): input
        # actions that need it fail like WindowsBackend's preflight. ``focus``
        # clears it unless sticky (an always-on-top window focus cannot fix).
        self.window_issue: str | None = None
        self.window_issue_sticky: bool = False

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
                "title": f"{self.form_title} - Microsoft Edge",
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

    async def dump_tree(self, target: str, *, max_nodes: int = 400) -> str:
        obs = await self.observe(target, ["uia", "app"])
        lines = [f"=== mock UIA dump: target={target!r} ===", f"app: {obs.app}"]
        lines += [f"- [{el.role}] id={el.element_id!r} name={el.name!r}" for el in obs.uia[:max_nodes]]
        return "\n".join(lines)

    def _is_form_target(self, target: str) -> bool:
        return "form" in (target or "").lower() or "edge" in (target or "").lower()

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
            ]
            for key, label in FORM_FIELDS.items():
                # Edge exposes the <label> as a Text node with the same Name as the Edit.
                uia.append(ElementRef(element_id=f"{label.replace(' ', '_')}_label", role="Text", name=label))
                uia.append(ElementRef(element_id=f"{key}_edit", role="Edit", name=label, auto_id=key))
            uia.append(
                ElementRef(
                    element_id=f"{FORM_SUBMIT[0]}_btn",
                    role="Button",
                    name=FORM_SUBMIT[1],
                    patterns=("Invoke",),
                    auto_id=FORM_SUBMIT[0],
                )
            )
            if self.form_result:
                uia.append(ElementRef(element_id="result_text", role="Text", name=self.form_result))
        errors = []
        if self.window_issue in _NEEDS["observe"]:
            errors.append(self._window_error(target or "Mock", "observe"))
        if "screenshot" in include:
            screenshot = ImageRef(path=None, data=b"", capture_quality="full")
        if "app" in include:
            if self._is_form_target(target):
                app = {
                    "target": target,
                    "title": f"{self.form_title} - Microsoft Edge",
                    "display": None,
                    "page": self.form_title,
                    "url": "file:///C:/wincua/examples/fixtures/form.html"
                    + ("#submitted" if self.form_result.startswith("OK:") else ""),
                    "fields": {FORM_FIELDS[k]: v for k, v in self.form.items()},
                }
            else:
                app = {
                    "display": f"Display is {self.display}",
                    "target": target,
                    "title": target or "Mock",
                }
        return Observation(uia=uia, screenshot=screenshot, app=app, errors=errors)

    def _window_error(self, title: str, need: str) -> StructuredError:
        assert self.window_issue is not None
        return window_error(
            self.window_issue,
            f"Window {title!r}: simulated {self.window_issue}.",
            {"title": title, "need": need, "auto_focus": True},
        )

    def _window_check(self, action: Action) -> ActionResult | None:
        """Fail an input action the way WindowsBackend's preflight would."""
        from wincua_driver.types import Semantic

        if self.window_issue is None:
            return None
        if isinstance(action, Semantic):
            if action.name in ("focus", "app.focus", "launch", "app.launch"):
                if not self.window_issue_sticky:
                    self.window_issue = None
                return None
            need = {"uia.invoke": "pattern", "invoke_by": "pattern", "uia.type": "keyboard", "type_by": "keyboard"}.get(
                action.name
            )
        else:
            need = {Invoke: "pattern", Type: "keyboard", Key: "keyboard", Click: "pointer"}.get(type(action))
        if need is None or self.window_issue not in _NEEDS[need]:
            return None
        return ActionResult(ok=False, mode_used="mock", error=self._window_error("Calculator", need))

    def _allow_check(self, name: str, args: dict[str, Any]) -> StructuredError | None:
        """launch / open_url obey a non-empty allow-list like WindowsBackend (an empty one stays permissive)."""
        if not self.allow:
            return None
        from wincua_driver.backend.windows import check_process_allowed

        if name in ("launch", "app.launch"):
            app = str(args.get("app") or args.get("exe") or "")
            return check_process_allowed(app, self.allow) if app else None
        if name in ("open_url", "browser.open"):
            preferred = "chrome.exe" if "chrome" in str(args.get("browser") or "").lower() else "msedge.exe"
            other = "msedge.exe" if preferred == "chrome.exe" else "chrome.exe"
            denied = check_process_allowed(preferred, self.allow)
            return denied if denied is not None and check_process_allowed(other, self.allow) is not None else None
        return None

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
            expr = "".join(self._expr)
            if m := re.fullmatch(r"(\d+)\+(\d+)", expr):
                self.display = str(int(m.group(1)) + int(m.group(2)))
            else:
                self.display = expr
            self._expr.clear()

    # --- browser form -----------------------------------------------------

    @staticmethod
    def _form_key(ids: Sequence[str | None], targets: dict[str, str]) -> str | None:
        """Map element ids (auto:<id>, name:<label>, snapshot <id>_edit, bare id) to a form key."""
        for raw in ids:
            if not raw:
                continue
            eid = str(raw)
            if eid.startswith("auto:"):
                eid = eid[5:]
            elif eid.startswith("name:"):
                label = eid[5:]
                eid = next((k for k, v in targets.items() if v == label), label)
            eid = re.sub(r"_(edit|btn)$", "", eid)
            if eid in targets:
                return eid
        return None

    def _field_for(self, *ids: str | None) -> str | None:
        return self._form_key(ids, FORM_FIELDS)

    def _is_submit(self, *ids: str | None) -> bool:
        return self._form_key(ids, {FORM_SUBMIT[0]: FORM_SUBMIT[1]}) is not None

    def _type_form(self, field: str | None, text: str) -> None:
        if field is None:
            # No element: keystrokes go to the focused field, else the first empty one.
            field = self._form_focus or next((k for k, v in self.form.items() if not v), None)
        if field is None:
            return
        self.form[field] = text
        self._form_focus = field

    def _submit_form(self) -> None:
        name, email = self.form["fullName"].strip(), self.form["email"].strip()
        if not name or not email:
            self.form_result = "Please fill name and email."
            return
        self.form_result = f"OK: {name} <{email}>"
        self.form_title = f"{FORM_TITLE} — {self.form_result}"

    def _not_found(self, element_id: str) -> ActionResult:
        return ActionResult(
            ok=False,
            mode_used="mock",
            error=StructuredError(code="element_not_found", message=f"No element for id={element_id!r}"),
        )

    async def act(self, action: Action, *, dry_run: bool = False) -> ActionResult:
        from wincua_driver.types import Semantic

        self.actions.append(action)
        mode = "dry-run" if dry_run else "mock"
        if dry_run:
            return ActionResult(ok=True, mode_used=mode)
        if (blocked := self._window_check(action)) is not None:
            return blocked
        if isinstance(action, Semantic):
            args = action.args or {}
            if (denied := self._allow_check(action.name, args)) is not None:
                return ActionResult(ok=False, mode_used=mode, error=denied)
            if action.name in ("wait", "system.wait", "launch", "app.launch", "focus", "app.focus"):
                return ActionResult(ok=True, mode_used=mode)
            if action.name in ("open_url", "browser.open"):
                title = args.get("target") or args.get("title") or FORM_TITLE
                return ActionResult(
                    ok=True,
                    mode_used=mode,
                    info={"title": f"{title} - Microsoft Edge", "document": title, "document_ready": True},
                )
            ids = (args.get("element_id"), args.get("auto_id") and f"auto:{args['auto_id']}", args.get("name") and f"name:{args['name']}")
            if action.name in ("uia.invoke", "invoke_by"):
                self._apply_calc_button(args.get("auto_id"))
                if self._is_submit(*ids):
                    self._submit_form()
                return ActionResult(ok=True, mode_used=mode)
            if action.name in ("uia.type", "type_by"):
                text = str(args.get("text") or "")
                field = self._field_for(*ids)
                if field is None and any(ids):
                    return self._not_found(str(next(i for i in ids if i)))
                self._type_form(field, text)
                if args.get("submit"):
                    self._submit_form()
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
            if self._is_submit(eid):
                self._submit_form()
            return ActionResult(ok=True, mode_used=mode)
        if isinstance(action, Type):
            field = self._field_for(action.element_id)
            if field is None and action.element_id:
                return self._not_found(action.element_id)
            self._type_form(field, action.text)
            if action.submit:
                self._submit_form()
            return ActionResult(ok=True, mode_used=mode)
        if isinstance(action, Click):
            if (action.element_id or "").startswith("auto:"):
                self._apply_calc_button(action.element_id[5:])
            if self._is_submit(action.element_id):
                self._submit_form()
            elif (field := self._field_for(action.element_id)) is not None:
                self._form_focus = field
            return ActionResult(ok=True, mode_used=mode)
        if isinstance(action, Key):
            keys = (action.keys or "").lower()
            if keys in {"enter", "{enter}"} or keys.endswith("enter"):
                self._submit_form()
            return ActionResult(ok=True, mode_used=mode)
        return ActionResult(ok=True, mode_used=mode)
