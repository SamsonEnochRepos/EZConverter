"""Adapter contract implemented by every conversion engine."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.capability import Capability, Fidelity, validate_options
from ..domain.errors import ErrorCode, EZError
from ..sandbox import Limits

ProgressFn = Callable[[float | None, str | None], None]


@dataclass(frozen=True)
class InputInfo:
    path: Path
    filename: str
    format: str
    size: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionContext:
    capability: Capability
    input: InputInfo
    options: dict[str, Any]
    output_dir: Path  # adapter writes exactly one output file here
    work_dir: Path  # private scratch space
    output_stem: str
    limits: Limits
    cancel: threading.Event
    progress: ProgressFn = lambda fraction, message: None
    threads: int = 2

    def output_path(self, extension: str) -> Path:
        return self.output_dir / f"{self.output_stem}.{extension}"


@dataclass
class ExecutionResult:
    output_path: Path
    warnings: list[str] = field(default_factory=list)
    tool_versions: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Assessment:
    """Input-specific refinement of a capability's static fidelity and warnings."""

    fidelity: Fidelity
    warnings: list[str] = field(default_factory=list)


@dataclass
class Availability:
    ok: bool
    reason: str | None = None
    version: str | None = None


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


class EngineAdapter(ABC):
    engine_id: str

    @abstractmethod
    def capabilities(self) -> list[Capability]:
        """Every capability this engine could provide if its dependencies are present."""

    @abstractmethod
    def availability(self, cap: Capability) -> Availability:
        """Whether this capability can run in the current environment (tools, codecs, modules)."""

    @abstractmethod
    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        """Run the conversion. Raise EZError on failure. Must honour ctx.cancel."""

    # ---- optional hooks with safe defaults

    def validate_options(self, cap: Capability, options: dict[str, Any] | None) -> dict[str, Any]:
        return validate_options(cap.options, options)

    def assess(self, cap: Capability, info: InputInfo, options: dict[str, Any]) -> Assessment:
        """May raise EZError(UNSUPPORTED_INPUT/...) for inputs this engine cannot handle."""
        return Assessment(cap.fidelity, list(cap.warnings))

    def extra_checks(self, ctx: ExecutionContext, result: ExecutionResult) -> list[Check]:
        """Engine-specific output verification, in addition to the generic per-format checks."""
        return []

    def describe(self) -> dict[str, Any]:
        return {"engine_id": self.engine_id}


def require_not_cancelled(ctx: ExecutionContext) -> None:
    if ctx.cancel.is_set():
        raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
