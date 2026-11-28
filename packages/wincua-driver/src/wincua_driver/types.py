"""Public observe/act types (stubs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence


@dataclass(frozen=True)
class ElementRef:
    element_id: str
    role: str = ""
    name: str = ""
    bounds: tuple[int, int, int, int] | None = None
    patterns: tuple[str, ...] = ()
    auto_id: str = ""  # UIA AutomationId; stable across observations, unlike element_id


@dataclass(frozen=True)
class ImageRef:
    path: str | None = None
    data: bytes | None = None
    capture_quality: str = "unknown"  # full | partial | failed | unknown


@dataclass(frozen=True)
class StructuredError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    hint: str = ""  # how a model or human can recover (e.g. "call focus, then retry")


@dataclass
class Observation:
    uia: list[ElementRef] = field(default_factory=list)
    screenshot: ImageRef | None = None
    app: dict[str, Any] | None = None
    errors: list[StructuredError] = field(default_factory=list)


@dataclass(frozen=True)
class Click:
    element_id: str | None = None
    x: int | None = None
    y: int | None = None


@dataclass(frozen=True)
class Type:
    text: str
    submit: bool = False
    element_id: str | None = None


@dataclass(frozen=True)
class Invoke:
    element_id: str


@dataclass(frozen=True)
class Key:
    keys: str  # e.g. "ctrl+s"


@dataclass(frozen=True)
class Semantic:
    name: str  # e.g. "excel.set_range"
    args: dict[str, Any] = field(default_factory=dict)


Action = Click | Type | Invoke | Key | Semantic

ModeUsed = Literal["background", "foreground", "dry-run", "mock", "null", "windows"]


@dataclass
class ActionResult:
    ok: bool
    mode_used: ModeUsed = "null"
    error: StructuredError | None = None
    # Optional success metadata (e.g. verified field value after type)
    info: dict[str, Any] | None = None
