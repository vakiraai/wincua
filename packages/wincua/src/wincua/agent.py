"""Agent loop — observe → plan → act / ask_human until done."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from wincua.guard import (
    LoopGuard,
    action_signature,
    normalize_model_args,
    observation_fingerprint,
    short_error,
)
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
    # None = no human (fail closed); "auto" | "ui" | "cli" | "off" builds a
    # transport from the [hitl] config; or pass a wincua_hitl transport.
    hitl: Any = None
    allow: Sequence[str] = ()
    mode: Mode = "foreground"
    recorder: bool = False
    backend: str | None = None
    # wincua.config.WincuaConfig, a path to wincua.toml, or None for the standard lookup.
    config: Any = None
    # LLM planners only: give up after this many failed tools / empty replies in a row.
    max_consecutive_errors: int = 5
    max_turns: int = 64
    observe_target: str = "foreground"
    # None = follow the transport's [hitl] confirm_risky_actions.
    confirm_risky: bool | None = None

    def __post_init__(self) -> None:
        self._apply_config()
        if self.provider is None:
            from wincua_providers import NullProvider

            self.provider = NullProvider()
        if self.hitl is not None:
            from wincua_hitl import make_transport

            self.hitl = make_transport(self.hitl)

    def _apply_config(self) -> None:
        """Build the provider from wincua.toml when none (or a name) is given.

        ``provider`` may be an instance, a provider name from the config
        (``"ollama"``, ``"anthropic"``…) or ``None`` for ``[provider].default``.
        Named / default providers get the config's ``fallback`` chain, which
        always ends in ``null`` (ask the human).
        """
        if not (self.provider is None or isinstance(self.provider, str) or self.config is not None):
            return
        from wincua.config import WincuaConfig, load_config

        if not isinstance(self.config, WincuaConfig):
            self.config = load_config(self.config)
        cfg = self.config
        if self.provider is None or isinstance(self.provider, str):
            self.provider = cfg.build_provider(self.provider)
        if self.backend is None and cfg.driver.backend:
            self.backend = cfg.driver.backend
        extra = [a for a in cfg.driver.allow if a not in self.allow]
        if extra:
            self.allow = [*self.allow, *extra]

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "name", type(self.provider).__name__)

    def _escalate(
        self, reason: str, turn: int, steps: list[dict[str, Any]], history: list[dict[str, Any]]
    ) -> bool:
        """Hand planning to the next provider in the fallback chain (ends in null = human)."""
        escalate = getattr(self.provider, "escalate", None)
        handed_over = bool(escalate and escalate(reason))
        steps.append(
            {"kind": "escalate", "turn": turn, "reason": reason, "to": self.provider_name if handed_over else None}
        )
        history.append({"kind": "escalation", "turn": turn, "text": f"Previous planner stopped: {reason}"})
        return handed_over

    def _apply_human_answer(self, answer: str, steps: list[dict[str, Any]]) -> tuple[bool, str] | None:
        """Answer to the null planner: finish, abort, or hand the instruction back to a model."""
        from wincua._terminal_prompt import classify_answer

        kind = classify_answer(answer)
        if kind == "done":
            steps.append({"kind": "done", "message": "done (human)", "by": "human"})
            return True, "done (human)"
        if kind == "abort":
            steps.append({"kind": "abort", "message": "aborted by human", "by": "human"})
            return False, "aborted by human"
        resume = getattr(self.provider, "resume", None)
        resumed = bool(resume and resume(f"human instruction: {answer}"))
        steps.append({"kind": "resume", "instruction": answer, "to": self.provider_name if resumed else None})
        return None

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

        timeout = getattr(getattr(self.config, "driver", None), "launch_timeout_s", None)
        if name == "windows" and timeout:
            from wincua_driver.backend.windows import WindowsBackend

            set_backend(WindowsBackend(allow=allow, launch_timeout_s=timeout))
        elif name == "windows":
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
        With ``recorder=True`` every step (observation summary, raw model reply,
        parsed actions, tool results, human answers, errors) is appended to
        ``recordings/<session>.jsonl`` as it happens, ending with a ``result`` line.
        """
        session = Session(
            allow=list(self.allow),
            mode=self.mode,
            recorder=self.recorder,
            hitl=self.hitl,
            provider=self.provider,
        )
        await session.start()
        steps = _RecordedSteps(session.record)
        steps.append(
            {
                "kind": "session_start",
                "session_id": session.session_id,
                "provider": self.provider_name,
                "mode": self.mode,
                "backend": self.backend,
                "observe_target": self.observe_target,
                "max_turns": self.max_turns,
            }
        )
        steps.append({"kind": "task", "task": self.task})
        try:
            result = await self._run(session, steps)
        except BaseException as exc:
            session.record({"kind": "crash", "error": f"{type(exc).__name__}: {exc}"})
            raise
        session.record({"kind": "result", "ok": result.ok, "message": result.message})
        result.recording_path = session.recording_path
        return result

    async def _run(self, session: Session, steps: list[dict[str, Any]]) -> AgentResult:
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
        error_streak = 0
        guard = LoopGuard()
        human_note: str | None = None

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
            session.record(
                {
                    "kind": "observation",
                    "turn": turn,
                    "elements": [
                        {"id": el.element_id, "role": el.role, "name": el.name, "auto_id": el.auto_id}
                        for el in obs.uia[:_RECORDED_ELEMENTS]
                    ],
                    "errors": [{"code": e.code, "message": getattr(e, "message", None)} for e in obs.errors],
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
                    "provider": plan.provider or self.provider_name,
                    "available": plan.available,
                    "plan_steps": [
                        {"kind": s.kind, "name": s.name, "args": s.args, "message": s.message}
                        for s in plan.steps
                    ],
                    "error": plan.error,
                    "text": plan.text,
                    "raw": plan.raw,
                }
            )
            recovers = bool(getattr(self.provider, "recovers_from_errors", False))

            if not plan.steps and plan.available and recovers:
                # Model answered without a tool call: show it the nudge and retry.
                history.append({"kind": "model_text", "turn": turn, "text": plan.text or ""})
                error_streak += 1
                if error_streak < self.max_consecutive_errors:
                    continue
                reason = f"model returned no tool call {error_streak} times in a row"
                if self._escalate(reason, turn, steps, history):
                    error_streak = 0
                    guard.reset()
                    continue
                recording = await session.end()
                return AgentResult(
                    ok=False,
                    steps=steps,
                    recording_path=recording,
                    message=f"model returned no tool call {error_streak} times in a row",
                )

            if not plan.steps:
                # NullProvider-style: route to HITL once, then abort if no answer
                if not plan.available:
                    answer = await self._ask_human(
                        question=plan.error or f"How should we proceed with: {self.task}?",
                        risk="info",
                        observation=last_obs,
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
            turn_sigs: list[str] = []
            stuck: str | None = None

            queue: list[PlanStep] = list(plan.steps)
            human_steps: set[int] = set()
            while queue:
                pstep = queue.pop(0)
                assert isinstance(pstep, PlanStep)
                by_human = id(pstep) in human_steps
                history.append(
                    {"kind": pstep.kind, "name": pstep.name, "args": pstep.args}
                )
                entry = history[-1]
                entry.update(call_id=pstep.call_id, turn=turn)
                if recovers and pstep.kind == "tool":
                    pstep.args = normalize_model_args(
                        pstep.name or "", dict(pstep.args or {}), last_obs, current_target=target
                    )
                    entry["args"] = pstep.args

                if (
                    pstep.kind == "ask_human"
                    and (pstep.args or {}).get("mode") == "next_step"
                    and self.hitl is not None
                ):
                    question = (pstep.args or {}).get("question")
                    if human_note:
                        question = f"{human_note}\n{question or 'Choose the next action.'}"
                        human_note = None
                    resp = await self._human_next_step(question=question, observation=last_obs)
                    steps.append(_human_record("next_step", resp))
                    if resp is None:
                        finished, ok = True, False
                        message = "HITL unavailable / no answer (fail-closed)"
                        break
                    if resp.kind == "takeover":
                        resp = await self._takeover(steps, last_obs)
                        if resp is None:
                            finished, ok = True, False
                            message = "HITL unavailable / no answer (fail-closed)"
                            break
                    if resp.kind == "action" and getattr(resp, "actions", None):
                        chained = [
                            PlanStep(kind="tool", name=a["tool"], args=dict(a.get("args") or {}))
                            for a in resp.actions
                        ]
                        human_steps.update(id(c) for c in chained)
                        queue[0:0] = chained
                        continue
                    follow = _step_from_response(resp, PlanStep)
                    if follow is not None:
                        human_steps.add(id(follow))
                        queue.insert(0, follow)
                    elif resp.kind == "instruction":
                        history.append({"kind": "human_instruction", "text": resp.text})
                        resume = getattr(self.provider, "resume", None)
                        if getattr(resp, "note", None):
                            human_note = resp.note
                        elif resume and resume(f"human instruction: {resp.text}"):
                            steps.append({"kind": "resume", "instruction": resp.text, "to": self.provider_name})
                            error_streak = 0
                            guard.reset()
                        elif self.provider_name == "null":
                            human_note = (
                                f"No model is running, so {resp.text!r} was not executed. "
                                "Use a command or pick an action."
                            )
                    continue

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
                        observation=last_obs,
                    )
                    steps.append({"kind": "ask_human", "answer": answer})
                    entry["result"] = {"answer": answer}
                    if answer is None:
                        finished = True
                        ok = False
                        message = "HITL unavailable / no answer (fail-closed)"
                        break
                    if self.provider_name == "null":
                        outcome = self._apply_human_answer(answer, steps)
                        if outcome is not None:
                            finished, (ok, message) = True, outcome
                            break
                        error_streak = 0
                        guard.reset()
                    continue

                # tool (or bare name treated as tool)
                tool_name = pstep.name or pstep.kind
                args = dict(pstep.args or {})
                planner_risk = args.pop("risk", None)
                if not by_human and self._should_confirm():
                    from wincua_hitl import risk_reason

                    reason = risk_reason(tool_name, {**args, "risk": planner_risk})
                    if reason:
                        decision = await self.hitl.confirm(
                            action={"tool": tool_name, "args": args},
                            reason=reason,
                            observation=last_obs,
                            task=self.task,
                        )
                        steps.append(
                            {**_human_record("confirm", decision), "tool": tool_name, "reason": reason}
                        )
                        if decision is None:
                            finished, ok = True, False
                            message = "risky action not confirmed (fail-closed)"
                            break
                        if decision.kind == "takeover":
                            follow_up = await self._takeover(steps, last_obs)
                            if follow_up is None:
                                finished, ok = True, False
                                message = "HITL unavailable / no answer (fail-closed)"
                                break
                            nxt = _step_from_response(follow_up, PlanStep)
                            if nxt is not None:
                                human_steps.add(id(nxt))
                                queue.insert(0, nxt)
                            continue
                        if not decision.approved:
                            history.append({"kind": "rejected", "name": tool_name, "args": args})
                            continue
                if (
                    by_human
                    and tool_name in ("invoke", "type", "type_text")
                    and not args.get("target")
                    and target != "foreground"
                ):
                    args["target"] = target
                if (
                    by_human
                    and tool_name in ("type", "type_text", "key", "hotkey")
                    and not args.get("element_id")
                    and target != "foreground"
                    and not dry_run
                ):
                    # Keystrokes go to the focused window, which in CLI mode is
                    # the terminal the human is typing in.
                    await act(Semantic(name="focus", args={"title": target, "timeout_s": 5}))
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
                if by_human:
                    result_record["by"] = "human"
                result_record.setdefault("args", args)
                result_record["turn"] = turn
                logged = bool(result_record.get("detail") and (self.recorder or by_human))
                if logged:
                    result_record["detail_log"] = _write_detail(
                        session.session_id, turn, tool_name, result_record["detail"]
                    )
                steps.append(result_record)
                if logged:
                    result_record.pop("detail")
                entry["result"] = {
                    k: result_record[k]
                    for k in ("ok", "code", "message", "hint", "display", "info")
                    if result_record.get(k) is not None
                }
                if by_human:
                    # Failures go back to the human on the next prompt; no guard / escalation.
                    if result_record.get("ok") is False:
                        log = result_record.get("detail_log")
                        code = result_record.get("code")
                        human_note = (
                            f"Last action {tool_name} failed"
                            + (f" [{code}]" if code else "")
                            + f": {result_record.get('message')}"
                            + (f" Hint: {result_record['hint']}" if result_record.get("hint") else "")
                            + (f" (full details: {log})" if log else "")
                        )
                        skipped = [q for q in queue if id(q) in human_steps]
                        if skipped:
                            queue = [q for q in queue if id(q) not in human_steps]
                            human_note += f" Skipped the remaining {len(skipped)} chained step(s)."
                    else:
                        shown = {k: v for k, v in args.items() if k != "target"}
                        ran = f"Ran {tool_name} {shown}: ok."
                        human_note = f"{human_note} {ran}" if human_note and human_note.startswith("Ran ") else ran
                else:
                    if recovers:
                        # LLM planners see failures in history and re-plan; the guard
                        # hands over to the next provider when they go in circles.
                        sig = action_signature(tool_name, args)
                        turn_sigs.append(sig)
                        stuck = guard.record_action(sig, result_record.get("ok") is not False)
                        if result_record.get("ok") is False:
                            break
                    if result_record.get("ok") is False:
                        finished = True
                        ok = False
                        message = result_record.get("message") or "tool failed"
                        if result_record.get("code"):
                            message = f"[{result_record['code']}] {message}"
                        if result_record.get("hint"):
                            message = f"{message} Hint: {result_record['hint']}"
                        break
                error_streak = 0
                # Allow scripts to retarget observe
                if tool_name in ("observe", "focus", "launch", "open_url") and args.get(
                    "target"
                ):
                    target = str(args["target"])
                elif tool_name in ("launch", "focus") and args.get("title"):
                    target = str(args["title"])
                elif tool_name == "open_url":
                    target = str(args.get("target") or target)

            if not finished and recovers:
                stuck = stuck or guard.end_turn(observation_fingerprint(last_obs), turn_sigs)
                if not stuck and guard.warning:
                    history.append({"kind": "guard_note", "turn": turn, "text": guard.warning})
                    steps.append({"kind": "guard_note", "turn": turn, "text": guard.warning})
                if stuck:
                    if self._escalate(stuck, turn, steps, history):
                        error_streak = 0
                        guard.reset()
                    else:
                        finished, ok, message = True, False, f"stopped: {stuck}"

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

    async def _ask_human(
        self, *, question: str, risk: str = "info", observation: Any = None
    ) -> str | None:
        if self.hitl is None:
            from wincua._terminal_prompt import ask_on_terminal

            return await ask_on_terminal(question)
        return await self.hitl.prompt(
            question=question, risk=risk, observation=observation, task=self.task
        )

    def _should_confirm(self) -> bool:
        if self.hitl is None:
            return False
        if self.confirm_risky is not None:
            return self.confirm_risky
        return bool(getattr(self.hitl, "confirm_risky_actions", True))

    async def _human_next_step(self, *, question: str | None, observation: Any) -> Any:
        if self.hitl is None:
            return None
        return await self.hitl.next_step(
            task=self.task, observation=observation, question=question
        )

    async def _takeover(self, steps: list[dict[str, Any]], observation: Any) -> Any:
        """Human drives the desktop; returns the resume/done/abort response."""
        resp = await self.hitl.takeover(task=self.task, observation=observation)
        steps.append(_human_record("takeover", resp))
        return resp

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
            return _tool_result("wait", result)

        if name in ("launch", "app.launch"):
            result = await act_fn(
                semantic_cls(name="launch", args=args),
                dry_run=dry_run,
            )
            return _tool_result("launch", result)

        if name in ("focus", "app.focus"):
            result = await act_fn(
                semantic_cls(name="focus", args=args),
                dry_run=dry_run,
            )
            return _tool_result("focus", result)

        if name in ("open_url", "browser.open"):
            if not str(args.get("url") or "").strip():
                return {
                    "kind": "tool",
                    "name": "open_url",
                    "ok": False,
                    "code": "invalid_args",
                    "message": "open_url needs url: the page address from the task (launching the browser alone opens no page)",
                }
            result = await act_fn(
                semantic_cls(name="open_url", args=args),
                dry_run=dry_run,
            )
            return _tool_result("open_url", result)

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
            if eid and str(eid).startswith("auto:") and not args.get("auto_id"):
                args["auto_id"] = str(eid)[5:]
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
            if eid and str(eid).startswith("auto:") and not args.get("auto_id"):
                args = {**args, "auto_id": str(eid)[5:]}
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
            "message": (
                f"Unknown tool: {tool_name!r} (use launch, focus, open_url, invoke, type, "
                "click, key, wait, ask_human, done or abort)"
            ),
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


_RECORDED_ELEMENTS = 200


class _RecordedSteps(list):
    """``steps`` list that also appends each new step to the session recording."""

    def __init__(self, record: Any) -> None:
        super().__init__()
        self._record = record

    def append(self, step: dict[str, Any]) -> None:
        super().append(step)
        self._record(step)


def _write_detail(session_id: str | None, turn: int, tool: str, text: str) -> str | None:
    """Append a full driver error (e.g. UIA tree dump) to recordings/<session>-errors.log."""
    from pathlib import Path

    path = Path("recordings") / f"{session_id or 'session'}-errors.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"--- turn {turn} {tool}\n{text}\n")
    except OSError:
        return None
    return str(path)


def _tool_result(name: str, result: Any) -> dict[str, Any]:
    error = None if result.ok else result.error
    full = error.message if error else None
    out: dict[str, Any] = {
        "kind": "tool",
        "name": name,
        "ok": result.ok,
        "mode_used": result.mode_used,
        # Short text for models and logs; the full driver message (UIA dumps…) stays in "detail".
        "message": short_error(full),
    }
    if error is not None:
        out["code"] = error.code
        if getattr(error, "hint", ""):
            out["hint"] = error.hint
    if full and full != out["message"]:
        out["detail"] = full
    info = getattr(result, "info", None)
    if info:
        out["info"] = info
    return out


def _human_record(kind: str, resp: Any) -> dict[str, Any]:
    return {
        "kind": "human",
        "request": kind,
        "response": resp.kind if resp is not None else "timeout",
        "action": getattr(resp, "action", None),
        "text": getattr(resp, "text", None),
    }


def _step_from_response(resp: Any, step_cls: Any) -> Any:
    """HITL response -> PlanStep to run next, or None (resume / instruction)."""
    if resp.kind == "action" and resp.action:
        return step_cls(kind="tool", name=resp.action["tool"], args=dict(resp.action.get("args") or {}))
    if resp.kind == "done":
        return step_cls(kind="done", message=resp.text or "human marked the task done")
    if resp.kind == "abort":
        return step_cls(kind="abort", message=resp.text or "human aborted")
    return None
