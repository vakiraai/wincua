"""wincua MCP server (FastMCP): observe / act / launch / list_windows / ask_human."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ContentBlock, TextContent, ToolAnnotations
from wincua_driver.types import (
    Action,
    ActionResult,
    Click,
    ElementRef,
    Invoke,
    Key,
    Observation,
    Semantic,
    StructuredError,
    Type,
)

from wincua_mcp import __version__
from wincua_mcp.hitl import HitlHook, confirm_action, default_hitl_hook
from wincua_mcp.policy import AllowList, classify_risk, exe_key, is_calculator_target, resolve_window
from wincua_mcp.settings import McpSettings

SERVER_NAME = "wincua"

INSTRUCTIONS = """\
wincua drives real Windows desktop apps through UI Automation.
Typical loop: list_windows → launch (if needed) → observe → act → observe to verify.
Prefer element ids returned by observe (or `auto:<AutomationId>` / `name:<Name>`) over x/y clicks.
Only processes on the configured allow-list can be launched, observed or acted on.
Risky actions (submits, destructive keys, coordinate clicks) may block for human approval.
Use ask_human when the task is ambiguous or needs information only the user has.
"""

# Roles worth showing even without a name (agents can still target them by id).
_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "edit",
        "checkbox",
        "radiobutton",
        "combobox",
        "listitem",
        "menuitem",
        "tabitem",
        "hyperlink",
        "slider",
        "spinner",
        "treeitem",
        "document",
    }
)


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if details:
        err["details"] = details
    return {"ok": False, "error": err}


def _structured_error(err: StructuredError) -> dict[str, Any]:
    out: dict[str, Any] = {"code": err.code, "message": err.message}
    if err.details:
        out["details"] = {k: v for k, v in err.details.items() if k != "tree"}
    return out


_NAME_FIRST_ROLES = frozenset({"edit", "combobox", "button", "hyperlink", "checkbox", "radiobutton", "menuitem"})


def stable_ids(elements: list[ElementRef]) -> dict[str, str]:
    """Snapshot id -> id that survives re-observation: ``auto:<AutomationId>``, else ``name:<Name>``.

    Snapshot ids (``fullName_12``) are renumbered on every walk. A Name shared by
    a label and its input goes to the input; later duplicates keep the snapshot id.
    """
    out: dict[str, str] = {}
    used: set[str] = set()
    for ref in elements:
        eid = ref.element_id
        auto = ref.auto_id or (eid[5:] if eid.startswith("auto:") else "")
        if eid.startswith(("auto:", "name:")):
            out[eid] = eid
        elif auto and f"auto:{auto}" not in used:
            out[eid] = f"auto:{auto}"
        else:
            continue
        used.add(out[eid])
    for interactive in (True, False):
        for ref in elements:
            if ref.element_id in out or not ref.name:
                continue
            if ((ref.role or "").lower() in _NAME_FIRST_ROLES) != interactive:
                continue
            key = f"name:{ref.name}"
            if key not in used:
                out[ref.element_id] = key
                used.add(key)
    for ref in elements:
        out.setdefault(ref.element_id, ref.element_id)
    return out


def _element_dict(ref: ElementRef, element_id: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"id": element_id or ref.element_id, "role": ref.role}
    if ref.name:
        d["name"] = ref.name
    if ref.bounds:
        d["bounds"] = list(ref.bounds)
    if ref.patterns:
        d["patterns"] = list(ref.patterns)
    return d


def trim_elements(
    elements: list[ElementRef],
    *,
    max_elements: int,
    roles: list[str] | None = None,
    name_contains: str | None = None,
) -> tuple[list[ElementRef], int]:
    """Drop unnamed non-interactive nodes, apply filters, cap the count. Returns (kept, total_matching)."""
    want_roles = {r.lower() for r in roles} if roles else None
    needle = name_contains.lower() if name_contains else None
    kept: list[ElementRef] = []
    for ref in elements:
        role = (ref.role or "").lower()
        if not ref.name and not ref.patterns and role not in _INTERACTIVE_ROLES:
            continue
        if want_roles is not None and role not in want_roles:
            continue
        if needle is not None and needle not in (ref.name or "").lower():
            continue
        kept.append(ref)
    return kept[:max_elements], len(kept)


@dataclass
class WincuaMcpRuntime:
    """Per-server state: driver backend, allow-list, HITL hook, current target window."""

    settings: McpSettings
    backend: Any = None
    hitl: HitlHook = field(default_factory=default_hitl_hook)
    current_target: str | None = None
    focused_target: str | None = None
    # Window handle behind current_target: browsers retitle the window (page
    # title changes on submit), so the old title alone stops matching.
    current_handle: int | None = None
    elements: dict[str, ElementRef] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.allow = AllowList(self.settings.effective_allow)
        if self.backend is not None and hasattr(self.backend, "set_allow"):
            self.backend.set_allow(self.allow.entries)

    def ensure_backend(self) -> Any:
        if self.backend is not None:
            return self.backend
        name = self.settings.backend
        if name == "windows":
            from wincua_driver.backend.windows import WindowsBackend

            self.backend = WindowsBackend(
                allow=self.allow.entries,
                launch_timeout_s=self.settings.launch_timeout_s,
            )
        elif name == "mock":
            from wincua_driver.backend.mock import MockBackend

            self.backend = MockBackend()
            self.backend.set_allow(self.allow.entries)
        else:
            from wincua_driver.backend.null import NullBackend

            self.backend = NullBackend()
        return self.backend

    async def windows(self) -> list[Mapping[str, Any]]:
        backend = self.ensure_backend()
        lister = getattr(backend, "list_windows", None)
        if lister is None:
            return []
        return list(await lister())

    async def check_target(self, target: str) -> tuple[Mapping[str, Any] | None, dict[str, Any] | None]:
        """Resolve ``target`` to a window and enforce the allow-list. Returns (window, error)."""
        if not self.allow:
            return None, _error(
                "allow_list_empty",
                "The allow-list is empty, so wincua-mcp refuses all desktop access. "
                "Add process names to [mcp].allow or [driver].allow in wincua.toml, or pass --allow.",
            )
        windows = await self.windows()
        window = resolve_window(windows, target)
        if window is None and self.current_handle and target == self.current_target:
            window = next((w for w in windows if w.get("handle") == self.current_handle), None)
        if window is None:
            allowed_titles = [w.get("title") for w in windows if self.allow.allows_window(w)]
            return None, _error(
                "window_not_found",
                f"No window matches target={target!r}. Use list_windows, or launch the app first.",
                target=target,
                allowed_windows=allowed_titles,
            )
        if not self.allow.allows_window(window):
            return None, _error(
                "allow_list_denied",
                f"Window {window.get('title')!r} belongs to {window.get('process')!r}, "
                "which is not on the allow-list.",
                process=window.get("process"),
                allow=self.allow.entries,
            )
        if window.get("handle"):
            self.current_handle = int(window["handle"])
        return window, None


def _result_dict(result: ActionResult) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": result.ok, "mode_used": result.mode_used}
    if result.error is not None:
        out["error"] = _structured_error(result.error)
    if result.info:
        out["info"] = result.info
    return out


def build_server(
    settings: McpSettings | None = None,
    *,
    backend: Any = None,
    hitl: HitlHook | None = None,
) -> FastMCP:
    """Create the FastMCP server. ``backend``/``hitl`` are injectable for tests."""
    settings = settings or McpSettings()
    runtime = WincuaMcpRuntime(settings=settings, backend=backend, hitl=hitl or default_hitl_hook())
    mcp = FastMCP(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        host=settings.http_host,
        port=settings.http_port,
        log_level="WARNING",
    )
    mcp._wincua_runtime = runtime  # type: ignore[attr-defined]

    @mcp.tool(
        annotations=ToolAnnotations(title="List windows", readOnlyHint=True, openWorldHint=False),
    )
    async def list_windows(only_allowed: bool = False) -> dict[str, Any]:
        """List visible top-level windows (title, process, pid) and whether each is on the allow-list."""
        try:
            windows = await runtime.windows()
        except Exception as exc:  # backend start failure (e.g. not on Windows)
            return _error("backend_unavailable", str(exc))
        rows = []
        for w in windows:
            allowed = runtime.allow.allows_window(w)
            if only_allowed and not allowed:
                continue
            rows.append(
                {
                    "title": w.get("title"),
                    "process": w.get("process"),
                    "pid": w.get("pid"),
                    "foreground": bool(w.get("foreground")),
                    "allowed": allowed,
                }
            )
        return {
            "ok": True,
            "windows": rows,
            "allow": runtime.allow.entries,
            "current_target": runtime.current_target,
        }

    @mcp.tool(
        annotations=ToolAnnotations(title="Launch app", readOnlyHint=False, destructiveHint=False),
    )
    async def launch(
        app: str | None = None,
        url: str | None = None,
        browser: str = "msedge",
        args: list[str] | None = None,
        title: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Launch an allow-listed app (e.g. "calc", "notepad.exe") or open `url` in a browser (Edge by default).

        `title` is the window title to wait for. On success the window becomes the current target for observe/act.
        """
        if bool(app) == bool(url):
            return _error("invalid_args", "Pass exactly one of `app` or `url`.")
        exe = app if app else browser
        if not runtime.allow:
            return _error(
                "allow_list_empty",
                "The allow-list is empty, so wincua-mcp refuses to launch anything.",
            )
        if not runtime.allow.allows_app(exe):
            return _error(
                "allow_list_denied",
                f"{exe_key(exe)!r} is not on the allow-list.",
                app=exe,
                allow=runtime.allow.entries,
            )
        try:
            backend = runtime.ensure_backend()
        except Exception as exc:
            return _error("backend_unavailable", str(exc))
        sem_args: dict[str, Any] = {}
        if timeout_s is not None:
            sem_args["timeout_s"] = timeout_s
        if url:
            sem_args.update(url=url, browser=browser)
            if title:
                sem_args["target"] = title
            action = Semantic("open_url", sem_args)
        else:
            sem_args.update(app=app, args=list(args or []))
            if title:
                sem_args["title"] = title
            action = Semantic("launch", sem_args)
        result = await backend.act(action)
        out = _result_dict(result)
        if result.ok:
            info = result.info or {}
            # Prefer the caller's title: it is a stable substring, the full window title is not.
            target = title or info.get("title")
            if not target and app and is_calculator_target(app):
                target = "Calculator"
            runtime.current_target = target or exe_key(exe)
            runtime.focused_target = runtime.current_target
            runtime.current_handle = None
            runtime.elements.clear()
            out["target"] = runtime.current_target
        return out

    @mcp.tool(
        structured_output=False,
        annotations=ToolAnnotations(title="Observe window", readOnlyHint=True, openWorldHint=False),
    )
    async def observe(
        target: str | None = None,
        include_screenshot: bool = False,
        max_elements: int | None = None,
        roles: list[str] | None = None,
        name_contains: str | None = None,
    ) -> list[ContentBlock]:
        """Return a trimmed UI Automation tree (id, role, name, bounds, patterns) for a window, plus an optional PNG screenshot.

        `target` is a window title (substring), "Calculator", or "foreground"; defaults to the current target.
        Filter with `roles` (e.g. ["Button", "Edit"]) or `name_contains`. Element ids feed `act`.
        """
        target = target or runtime.current_target or "foreground"
        try:
            backend = runtime.ensure_backend()
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps(_error("backend_unavailable", str(exc))))]
        window, err = await runtime.check_target(target)
        if err is not None:
            return [TextContent(type="text", text=json.dumps(err))]
        assert window is not None
        resolved = str(window.get("title") or target)
        include = ["uia", "app"] + (["screenshot"] if include_screenshot else [])
        obs: Observation = await backend.observe(resolved, include)
        runtime.current_target = resolved
        ids = stable_ids(obs.uia)
        runtime.elements = {ref.element_id: ref for ref in obs.uia}
        runtime.elements.update({ids[ref.element_id]: ref for ref in obs.uia})
        cap = max_elements or settings.max_elements
        kept, total = trim_elements(obs.uia, max_elements=cap, roles=roles, name_contains=name_contains)
        payload: dict[str, Any] = {
            "ok": not any(e.code == "window_not_found" for e in obs.errors),
            "target": resolved,
            "process": window.get("process"),
            "elements": [_element_dict(r, ids[r.element_id]) for r in kept],
            "element_count": total,
            "truncated": total > len(kept),
        }
        if obs.app:
            payload["app"] = obs.app
        if obs.errors:
            payload["errors"] = [_structured_error(e) for e in obs.errors]
        blocks: list[ContentBlock] = []
        if include_screenshot:
            shot = obs.screenshot
            payload["screenshot"] = {
                "capture_quality": shot.capture_quality if shot else "failed",
                "attached": bool(shot and shot.data),
            }
            if shot and shot.data:
                blocks.append(Image(data=shot.data, format="png").to_image_content())
        return [TextContent(type="text", text=json.dumps(payload)), *blocks]

    @mcp.tool(
        annotations=ToolAnnotations(title="Act on window", readOnlyHint=False, destructiveHint=True),
    )
    async def act(
        action: Literal["click", "type", "invoke", "key"],
        element_id: str | None = None,
        x: int | None = None,
        y: int | None = None,
        text: str | None = None,
        submit: bool = False,
        keys: str | None = None,
        target: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Perform one desktop action on the current (or given) target window.

        - click: `element_id`, or `x`/`y` screen coordinates (always needs approval when confirmation is on)
        - type: `text`, optional `element_id` to fill, `submit` to press Enter afterwards
        - invoke: `element_id` (UIA Invoke pattern, e.g. "auto:num7Button", "name:Submit form")
        - key: `keys` such as "enter", "ctrl+a", "escape"
        `dry_run` validates and checks policy without touching the desktop.
        """
        built: Action
        if action == "click":
            if element_id:
                built = Click(element_id=element_id)
            elif x is not None and y is not None:
                built = Click(x=x, y=y)
            else:
                return _error("invalid_args", "click needs `element_id` or both `x` and `y`.")
        elif action == "type":
            if text is None:
                return _error("invalid_args", "type needs `text`.")
            built = Type(text=text, submit=submit, element_id=element_id)
        elif action == "invoke":
            if not element_id:
                return _error("invalid_args", "invoke needs `element_id`.")
            built = Invoke(element_id=element_id)
        elif action == "key":
            if not keys:
                return _error("invalid_args", "key needs `keys`.")
            built = Key(keys=keys)
        else:
            return _error("invalid_args", f"Unknown action {action!r}.")

        want = target or runtime.current_target
        if not want:
            return _error("no_target", "No target window yet. Call launch or observe first, or pass `target`.")
        try:
            backend = runtime.ensure_backend()
        except Exception as exc:
            return _error("backend_unavailable", str(exc))
        window, err = await runtime.check_target(want)
        if err is not None:
            return err
        assert window is not None
        resolved = str(window.get("title") or want)

        out: dict[str, Any] = {"target": resolved}
        element = runtime.elements.get(element_id or "")
        risk = classify_risk(built, element_name=element.name if element else None)
        if risk:
            out["risk"] = risk
        if risk and settings.require_confirmation and not dry_run:
            decision = await confirm_action(
                runtime.hitl,
                f"{risk} in window {resolved!r}",
                timeout_s=settings.confirm_timeout_s,
                action=built,
            )
            out["confirmation"] = {"approved": decision.approved, "answer": decision.answer, "hitl": decision.hook}
            if not decision.approved:
                reason = "no answer (timeout or no HITL UI running)" if decision.answer is None else "denied"
                return {
                    **out,
                    **_error(
                        "confirmation_denied",
                        f"Risky action was not approved: {reason}. Ask the user, or retry once they approve.",
                    ),
                }

        if not dry_run and runtime.focused_target != resolved:
            focus = await backend.act(Semantic("focus", {"title": resolved}))
            if not focus.ok:
                return {**out, **_result_dict(focus)}
            runtime.focused_target = resolved
        runtime.current_target = resolved

        result = await backend.act(built, dry_run=dry_run)
        return {**out, **_result_dict(result)}

    @mcp.tool(
        annotations=ToolAnnotations(title="Ask the human", readOnlyHint=True, openWorldHint=True),
    )
    async def ask_human(
        question: str,
        options: list[str] | None = None,
        risk: Literal["info", "confirm", "credential"] = "info",
        timeout_s: float = 300,
    ) -> dict[str, Any]:
        """Ask the person at the desktop a question through the wincua HITL channel and wait for the answer.

        Returns `answered: false` on timeout or when no HITL UI is available (fail-closed).
        """
        answer = await runtime.hitl.ask(question, options=options, risk=risk, timeout_s=timeout_s)
        return {"ok": True, "answered": answer is not None, "answer": answer, "hitl": runtime.hitl.name}

    return mcp


def get_runtime(server: FastMCP) -> WincuaMcpRuntime:
    return server._wincua_runtime  # type: ignore[attr-defined]


def run(settings: McpSettings) -> None:
    server = build_server(settings)
    if settings.transport == "http":
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")


__all__ = [
    "INSTRUCTIONS",
    "SERVER_NAME",
    "WincuaMcpRuntime",
    "__version__",
    "build_server",
    "get_runtime",
    "run",
    "stable_ids",
    "trim_elements",
]
