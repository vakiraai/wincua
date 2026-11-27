"""Agent loop — observe → plan → act / ask_human until done."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from wincua.session import Session


Mode = Literal["foreground", "background", "dry-run"]


@dataclass
class AgentResult:
    ok: bool
    steps: list[dict[str, Any]] = field(default_factory=list)
    recording_path: str | None = None
    message: str | None = None


@dataclass
class Agent:
    task: str
    provider: Any = None
    hitl: Any = None
    allow: Sequence[str] = ()
    mode: Mode = "foreground"
    recorder: bool = False
    backend: str | None = None
    max_turns: int = 64
    observe_target: str = "foreground"

    def __post_init__(self) -> None:
        if self.provider is None:
            from wincua_providers import NullProvider

            self.provider = NullProvider()

    def _ensure_backend(self) -> None:
        from wincua_driver.backend import get_backend, set_backend
        from wincua_driver.backend.windows import is_windows

        allow = list(self.allow)
        name = self.backend
        if name is None:
            # Auto: windows on Win32 host, else leave current / null
            if is_windows():
                name = "windows"
            else:
                current = get_backend()
                if hasattr(current, "set_allow"):
                    current.set_allow(allow)  # type: ignore[attr-defined]
                return

        current = get_backend()
        # Reuse an already-selected backend of the same name so demos can
        # seed MockBackend state (or pass a preconfigured WindowsBackend).
        if getattr(current, "name", None) == name:
            if hasattr(current, "set_allow"):
                current.set_allow(allow)  # type: ignore[attr-defined]
            return

        if name == "windows":
            set_backend("windows", allow=allow)
        else:
            set_backend(name)
            backend = get_backend()
            if hasattr(backend, "set_allow"):
                backend.set_allow(allow)  # type: ignore[attr-defined]

    async def run(self) -> AgentResult:
        """Run observe → provider.plan → act / ask_human until done.

        Demos can pass ``ScriptedProvider`` so no LLM is required.
        On non-Windows, selecting the windows backend fails with a clear error.
        """
        from wincua_driver import (
            Click,
            Invoke,
            Key,
            Semantic,
            Type,
            act,
            observe,
        )
        from wincua_providers.base import PlanStep

        session = Session(
            allow=list(self.allow),
            mode=self.mode,
            recorder=self.recorder,
            hitl=self.hitl,
            provider=self.provider,
        )
        await session.start()
        steps: list[dict[str, Any]] = [
            {"kind": "session_start", "session_id": session.session_id},
            {"kind": "task", "task": self.task},
        ]

        try:
            self._ensure_backend()
        except RuntimeError as exc:
            recording = await session.end()
            steps.append({"kind": "error", "message": str(exc)})
            return AgentResult(
                ok=False,
                steps=steps,
                recording_path=recording,
                message=str(exc),
            )

        dry_run = self.mode == "dry-run"
        history: list[dict[str, Any]] = []
        last_obs: Any = None
        target = self.observe_target

        for turn in range(self.max_turns):
            obs = await observe(target=target, include=["uia", "app", "screenshot"])
            last_obs = obs
            steps.append(
                {
                    "kind": "observe",
                    "turn": turn,
                    "target": target,
                    "uia_count": len(obs.uia),
                    "app": obs.app,
                    "errors": [e.code for e in obs.errors],
                }
            )

            plan = await self.provider.plan(
                task=self.task,
                observation=obs,
                history=history,
            )
            steps.append(
                {
                    "kind": "plan",
                    "turn": turn,
                    "provider": getattr(self.provider, "name", type(self.provider).__name__),
                    "available": plan.available,
                    "plan_steps": [
                        {"kind": s.kind, "name": s.name, "args": s.args, "message": s.message}
                        for s in plan.steps
                    ],
                    "error": plan.error,
                }
            )

            if not plan.steps:
                # NullProvider-style: route to HITL once, then abort if no answer
                if not plan.available:
                    answer = await self._ask_human(
                        question=plan.error or f"How should we proceed with: {self.task}?",
                        risk="info",
                    )
                    steps.append({"kind": "ask_human", "answer": answer})
                    if answer is None:
                        recording = await session.end()
                        return AgentResult(
                            ok=False,
                            steps=steps,
                            recording_path=recording,
                            message="HITL unavailable / no answer (fail-closed)",
                        )
                recording = await session.end()
                return AgentResult(
                    ok=False,
                    steps=steps,
                    recording_path=recording,
                    message="provider returned no steps",
                )

            finished = False
            ok = True
            message: str | None = None

            for pstep in plan.steps:
                assert isinstance(pstep, PlanStep)
                history.append(
                    {"kind": pstep.kind, "name": pstep.name, "args": pstep.args}
                )

                if pstep.kind == "done":
                    finished = True
                    ok = True
                    message = pstep.message or "done"
                    steps.append({"kind": "done", "message": message})
                    break

                if pstep.kind == "abort":
                    finished = True
                    ok = False
                    message = pstep.message or "aborted"
                    steps.append({"kind": "abort", "message": message})
                    break

                if pstep.kind == "ask_human":
                    answer = await self._ask_human(
                        question=(pstep.args or {}).get("question")
                        or pstep.message
                        or self.task,
                        risk=(pstep.args or {}).get("risk", "info"),
                    )
                    steps.append({"kind": "ask_human", "answer": answer})
                    if answer is None:
                        finished = True
                        ok = False
                        message = "HITL unavailable / no answer (fail-closed)"
                        break
                    continue

                # tool (or bare name treated as tool)
                tool_name = pstep.name or pstep.kind
                args = dict(pstep.args or {})
                result_record = await self._dispatch_tool(
                    tool_name=tool_name,
                    args=args,
                    dry_run=dry_run,
                    click_cls=Click,
                    type_cls=Type,
                    invoke_cls=Invoke,
                    key_cls=Key,
                    semantic_cls=Semantic,
                    act_fn=act,
                    observe_fn=observe,
                    last_obs=last_obs,
                )
                steps.append(result_record)
                if result_record.get("ok") is False:
                    finished = True
                    ok = False
                    message = result_record.get("message") or "tool failed"
                    break
                # Allow scripts to retarget observe
                if tool_name in ("observe", "focus", "launch", "open_url") and args.get(
                    "target"
                ):
                    target = str(args["target"])
                elif tool_name == "launch" and args.get("title"):
                    target = str(args["title"])
                elif tool_name == "open_url":
                    target = str(args.get("target") or target)

            if finished:
                recording = await session.end()
                return AgentResult(
                    ok=ok,
                    steps=steps,
                    recording_path=recording,
                    message=message,
                )

        recording = await session.end()
        return AgentResult(
            ok=False,
            steps=steps,
            recording_path=recording,
            message=f"max_turns ({self.max_turns}) exceeded",
        )

    async def _ask_human(self, *, question: str, risk: str = "info") -> str | None:
        if self.hitl is None:
            return None
        return await self.hitl.prompt(question=question, risk=risk)

    async def _dispatch_tool(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        dry_run: bool,
        click_cls: Any,
        type_cls: Any,
        invoke_cls: Any,
        key_cls: Any,
        semantic_cls: Any,
        act_fn: Any,
        observe_fn: Any,
        last_obs: Any,
    ) -> dict[str, Any]:
        name = tool_name.replace("-", "_")

        if name == "observe":
            target = str(args.get("target") or self.observe_target)
            include = args.get("include") or ["uia", "app"]
            obs = await observe_fn(target=target, include=include)
            return {
                "kind": "tool",
                "name": "observe",
                "ok": True,
                "uia_count": len(obs.uia),
                "app": obs.app,
            }

        if name == "wait":
            result = await act_fn(
                semantic_cls(name="wait", args=args),
                dry_run=dry_run,
            )
            return {
                "kind": "tool",
                "name": "wait",
                "ok": result.ok,
                "mode_used": result.mode_used,
                "message": None if result.ok else (result.error.message if result.error else None),
            }

        if name in ("launch", "app.launch"):
            result = await act_fn(
                semantic_cls(name="launch", args=args),
                dry_run=dry_run,
            )
            return {
                "kind": "tool",
                "name": "launch",
                "ok": result.ok,
                "mode_used": result.mode_used,
                "message": None if result.ok else (result.error.message if result.error else None),
            }

        if name in ("focus", "app.focus"):
            result = await act_fn(
                semantic_cls(name="focus", args=args),
                dry_run=dry_run,
            )
            return {
                "kind": "tool",
                "name": "focus",
                "ok": result.ok,
                "mode_used": result.mode_used,
                "message": None if result.ok else (result.error.message if result.error else None),
            }

        if name in ("open_url", "browser.open"):
            result = await act_fn(
                semantic_cls(name="open_url", args=args),
                dry_run=dry_run,
            )
            return {
                "kind": "tool",
                "name": "open_url",
                "ok": result.ok,
                "mode_used": result.mode_used,
                "message": None if result.ok else (result.error.message if result.error else None),
            }

        if name in ("click",):
            result = await act_fn(
                click_cls(
                    element_id=args.get("element_id"),
                    x=args.get("x"),
                    y=args.get("y"),
                ),
                dry_run=dry_run,
            )
            return _tool_result("click", result)

        if name in ("type", "type_text"):
            eid = args.get("element_id")
            if not eid and args.get("auto_id"):
                eid = f"auto:{args['auto_id']}"
            if not eid and args.get("name"):
                eid = f"name:{args['name']}"
            # Prefer semantic type_by so Windows backend can resolve auto/name
            if args.get("auto_id") or args.get("name") or eid:
                result = await act_fn(
                    semantic_cls(
                        name="uia.type",
                        args={
                            "element_id": eid,
                            "auto_id": args.get("auto_id"),
                            "name": args.get("name"),
                            "text": args.get("text", ""),
                            "submit": args.get("submit", False),
                            "target": args.get("target"),
                        },
                    ),
                    dry_run=dry_run,
                )
            else:
                result = await act_fn(
                    type_cls(
                        text=str(args.get("text", "")),
                        submit=bool(args.get("submit", False)),
                        element_id=eid,
                    ),
                    dry_run=dry_run,
                )
            return _tool_result("type", result)

        if name in ("invoke", "uia.invoke"):
            eid = args.get("element_id")
            if not eid and args.get("auto_id"):
                eid = f"auto:{args['auto_id']}"
            if not eid and args.get("name"):
                eid = f"name:{args['name']}"
            result = await act_fn(
                semantic_cls(
                    name="uia.invoke",
                    args={
                        "element_id": eid,
                        "auto_id": args.get("auto_id"),
                        "name": args.get("name"),
                        "target": args.get("target"),
                    },
                ),
                dry_run=dry_run,
            )
            return _tool_result("invoke", result)

        if name in ("key", "hotkey"):
            result = await act_fn(key_cls(keys=str(args.get("keys", ""))), dry_run=dry_run)
            return _tool_result("key", result)

        if name in ("assert_display", "assert_text"):
            return await self._assert_text(args=args, observe_fn=observe_fn, last_obs=last_obs)

        if name == "semantic":
            result = await act_fn(
                semantic_cls(name=str(args.get("name", "")), args=args.get("args") or {}),
                dry_run=dry_run,
            )
            return _tool_result("semantic", result)

        return {
            "kind": "tool",
            "name": name,
            "ok": False,
            "message": f"Unknown tool: {tool_name!r}",
        }

    async def _assert_text(
        self,
        *,
        args: dict[str, Any],
        observe_fn: Any,
        last_obs: Any,
    ) -> dict[str, Any]:
        _ = last_obs
        target = str(args.get("target") or self.observe_target)
        obs = await observe_fn(target=target, include=["uia", "app"])
        expected = args.get("equals")
        contains = args.get("contains")
        auto_id = args.get("auto_id", "CalculatorResults")

        chunks: list[str] = []
        if obs.app:
            if obs.app.get("display"):
                chunks.append(str(obs.app["display"]))
            if obs.app.get("title"):
                chunks.append(str(obs.app["title"]))
        for el in obs.uia:
            if el.name:
                chunks.append(el.name)
            if auto_id and auto_id.lower() in (el.element_id or "").lower() and el.name:
                chunks.insert(0, el.name)

        hay = "\n".join(chunks)
        ok = True
        message = None
        matched = hay

        if expected is not None:
            exp = str(expected)
            # Calculator often reports "Display is 42"
            ok = any(
                (c == exp or c.endswith(exp) or bool(re.search(rf"(?:^|\D){re.escape(exp)}(?:\D|$)", c)))
                for c in chunks
            ) or bool(re.search(rf"(?:^|\D){re.escape(exp)}(?:\D|$)", hay))
            if not ok:
                message = f"assert_display expected {exp!r}, got {hay!r}"
            else:
                matched = next((c for c in chunks if exp in c), hay)

        if contains is not None and ok:
            needle = str(contains)
            ok = needle in hay
            if not ok:
                message = f"assert_text expected to contain {needle!r}, got {hay!r}"
            else:
                matched = next((c for c in chunks if needle in c), hay)

        return {
            "kind": "tool",
            "name": "assert_display",
            "ok": ok,
            "display": matched,
            "message": message,
            "app": obs.app,
        }


def _tool_result(name: str, result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "tool",
        "name": name,
        "ok": result.ok,
        "mode_used": result.mode_used,
        "message": None if result.ok else (result.error.message if result.error else None),
    }
    info = getattr(result, "info", None)
    if info:
        out["info"] = info
    return out
