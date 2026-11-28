"""Ask the human on the terminal when no HITL UI is configured."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, TextIO

DONE_WORDS = frozenset({"done", "finish", "finished", "stop"})
ABORT_WORDS = frozenset({"abort", "quit", "q", "cancel", "exit"})

HELP = (
    "Type an instruction for the model (e.g. 'press 4, then 2, then ='), "
    "'done' if the task is finished, or 'abort'. Empty answer = abort."
)


def is_interactive(stream: Any = None) -> bool:
    stream = sys.stdin if stream is None else stream
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, ValueError):
        return False


async def ask_on_terminal(
    question: str,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    force: bool = False,
) -> str | None:
    """Prompt on stdin/stdout. None when not attached to a TTY (fail closed) or no answer."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if not (force or is_interactive(stdin)):
        return None

    def _ask() -> str | None:
        stdout.write(f"\n[wincua] {question}\n  {HELP}\n> ")
        stdout.flush()
        try:
            line = stdin.readline()
        except (EOFError, KeyboardInterrupt, OSError):
            return None
        return line.strip() or None

    return await asyncio.to_thread(_ask)


def classify_answer(answer: str) -> str:
    """'done', 'abort' or 'instruction'."""
    word = answer.strip().lower().rstrip(".!")
    if word in DONE_WORDS:
        return "done"
    if word in ABORT_WORDS:
        return "abort"
    return "instruction"
