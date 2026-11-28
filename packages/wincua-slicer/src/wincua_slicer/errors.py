"""Structured errors for the 3D Slicer pack."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlicerError(Exception):
    """Base error for wincua-slicer operations."""

    code: str
    message: str
    hint: str | None = None

    def __str__(self) -> str:
        parts = [f"{self.code}: {self.message}"]
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return " — ".join(parts)


class SlicerNotInstalledError(SlicerError):
    """Raised when no Slicer executable can be resolved on this host."""

    def __init__(self) -> None:
        super().__init__(
            code="slicer_not_installed",
            message=(
                "3D Slicer was not found on this machine. "
                "Install from https://download.slicer.org/ (Windows 64-bit stable)."
            ),
            hint=(
                "Set SLICER_EXECUTABLE or WINCUA_SLICER_EXECUTABLE to the full path "
                "to Slicer.exe (e.g. C:\\Program Files\\Slicer 5.6.2\\Slicer.exe)."
            ),
        )


class SlicerNotSupportedError(SlicerError):
    """Raised when an operation requires Windows + a real Slicer binary."""

    def __init__(self, *, detail: str = "") -> None:
        msg = "wincua-slicer live operations require Windows with Slicer installed."
        if detail:
            msg = f"{msg} ({detail})"
        super().__init__(
            code="slicer_platform",
            message=msg,
            hint="Use --mock in examples/tests on Linux, or run on Win11 with Slicer.",
        )


class SlicerCommandError(SlicerError):
    """Raised when Slicer exits non-zero or times out."""

    def __init__(self, *, message: str, exit_code: int | None = None) -> None:
        code = "slicer_command_failed"
        hint = None
        if exit_code is not None:
            message = f"{message} (exit {exit_code})"
        super().__init__(code=code, message=message, hint=hint)