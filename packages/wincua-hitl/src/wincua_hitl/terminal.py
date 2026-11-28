"""CliUI — ``mode = "cli"`` fallback: ask in the terminal on stdin/stdout."""

from __future__ import annotations

import asyncio
import getpass
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, TextIO

from wincua_hitl.commands import COMMAND_HELP
from wincua_hitl.transport import HitlTransport
from wincua_hitl.types import HumanRequest, HumanResponse, image_mime

_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


class CliUI(HitlTransport):
    name = "cli"

    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        shot_dir: str | Path | None = None,
        max_elements: int = 25,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._in = stdin
        self._out = stdout
        self._shot_dir = Path(shot_dir) if shot_dir else Path(tempfile.gettempdir()) / "wincua-hitl"
        self.max_elements = max_elements
        self._lines: queue.Queue[str] | None = None
        self._eof = False

    @property
    def out(self) -> TextIO:
        return self._out or sys.stdout

    def _print(self, *parts: Any) -> None:
        print(*parts, file=self.out, flush=True)

    async def _read(self, prompt: str, timeout_s: float, *, secret: bool = False) -> str | None:
        self.out.write(prompt)
        self.out.flush()
        if secret and self._in is None:
            reader: Callable[[], str] = lambda: getpass.getpass("") + "\n"  # noqa: E731
            try:
                line = await asyncio.wait_for(asyncio.to_thread(reader), timeout=timeout_s)
            except asyncio.TimeoutError:
                self._print("\n(timed out - failing closed)")
                return None
        else:
            line = await self._next_line(timeout_s)
            if line is None:
                self._print("\n(timed out - failing closed)")
                return None
        if line == "":
            return None
        return line.rstrip("\r\n")

    async def _next_line(self, timeout_s: float) -> str | None:
        # One daemon reader thread for the stream's lifetime: a blocked
        # readline() must never be joined at interpreter/asyncio shutdown
        # (Ctrl+C or a timeout would otherwise hang), and must not race a
        # second reader for the next line.
        if self._lines is None:
            self._lines = queue.Queue()
            stream = self._in or sys.stdin
            lines = self._lines

            def pump() -> None:
                while True:
                    try:
                        line = stream.readline()
                    except (OSError, ValueError):
                        line = ""
                    lines.put(line)
                    if line == "":
                        return

            threading.Thread(target=pump, name="wincua-hitl-stdin", daemon=True).start()
        if self._eof:
            return ""
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                line = self._lines.get_nowait()
                self._eof = line == ""
                return line
            except queue.Empty:
                if time.monotonic() >= deadline:
                    return None
                await asyncio.sleep(0.05)

    def _save_shot(self, req: HumanRequest) -> Path | None:
        if not req.screenshot:
            return None
        try:
            self._shot_dir.mkdir(parents=True, exist_ok=True)
            path = self._shot_dir / f"{req.id}{_EXT.get(image_mime(req.screenshot), '.bin')}"
            path.write_bytes(req.screenshot)
            return path
        except OSError:
            return None

    def _show(self, req: HumanRequest) -> None:
        bar = "-" * 60
        self._print(f"\n{bar}\nwincua HITL - {req.kind}")
        if req.task:
            self._print(f"task: {req.task}")
        obs = req.observation or {}
        if obs.get("app"):
            self._print("app: " + ", ".join(f"{k}={v}" for k, v in obs["app"].items()))
        for err in obs.get("errors") or []:
            self._print(f"! {err.get('code')}: {err.get('message')}")
            if err.get("hint"):
                self._print(f"  hint: {err['hint']}")
        shot = self._save_shot(req)
        if shot:
            self._print(f"screenshot: {shot}")
        elements = obs.get("uia") or []
        if elements and req.kind != "next_step":
            self._print(f"elements ({len(elements)}):")
            for el in elements[: self.max_elements]:
                self._print(f"  {el['element_id']:<28} {el['role']:<12} {el['name']}")
        if req.action:
            self._print(f"the agent wants to run: {req.action['tool']} {req.action.get('args')}")
        if req.reason:
            self._print(f"why: {req.reason}")
        self._print(f"\n{req.question}")

    async def _exchange(self, req: HumanRequest, timeout_s: float) -> HumanResponse | None:
        self._show(req)
        if req.kind == "question":
            for i, opt in enumerate(req.options, 1):
                self._print(f"  [{i}] {opt}")
            line = await self._read("> ", timeout_s, secret=req.risk == "credential")
            if line is None:
                return None
            if line.strip().isdigit() and req.options:
                idx = int(line.strip()) - 1
                if 0 <= idx < len(req.options):
                    return HumanResponse(kind="answer", text=req.options[idx])
            if line.strip().lower() == "abort":
                return HumanResponse(kind="abort")
            return HumanResponse(kind="answer", text=line)

        if req.kind == "confirm":
            while True:
                self._print("Type y and press Enter to let it run, n to skip it, t to do it yourself.")
                line = await self._read("approve [y/n/t] > ", timeout_s)
                if line is None:
                    return None
                ans = line.strip().lower()
                if ans in ("y", "yes", "approve"):
                    return HumanResponse(kind="approve")
                if ans in ("n", "no", "reject", ""):
                    return HumanResponse(kind="reject")
                if ans in ("t", "takeover"):
                    return HumanResponse(kind="takeover")

        if req.kind == "takeover":
            line = await self._read("press Enter to resume, or type done / abort > ", timeout_s)
            if line is None:
                return None
            ans = line.strip().lower()
            if ans in ("done", "abort"):
                return HumanResponse(kind=ans)  # type: ignore[arg-type]
            return HumanResponse(kind="resume")

        for i, s in enumerate(req.suggestions, 1):
            self._print(f"  [{i:>2}] {s['label']:<24} = {_as_command(s)}")
        example = _example_command(req)
        self._print(
            "\nNothing happens until you type something and press Enter.\n"
            f"Type a number (e.g. 3), a button name (e.g. One), or a command (e.g. {example}, type 23).\n"
            "  chain with | or ;  e.g.  one | seven | plus\n"
            "  done   when the task is finished      abort   to stop\n"
            "  takeover   to use the mouse yourself, then Enter to hand back\n"
            f"  (all commands: {COMMAND_HELP})"
        )
        while True:
            line = await self._read("next > ", timeout_s)
            if line is None:
                return None
            if line.strip():
                return HumanResponse(kind="instruction", text=line)


def _as_command(s: dict) -> str:
    args = s.get("args") or {}
    eid = args.get("element_id", "")
    if s.get("tool") == "type":
        return f"type @{eid} <text>   (or: <number> <text>)"
    return f"{s.get('tool')} {eid}".strip()


def _example_command(req: HumanRequest) -> str:
    for el in (req.observation or {}).get("uia") or []:
        if str(el.get("element_id", "")).startswith("auto:num"):
            return f"invoke {el['element_id']}"
    if req.suggestions:
        return _as_command(req.suggestions[0])
    return "invoke auto:num1Button"
