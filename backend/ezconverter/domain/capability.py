"""Capability model: what an engine can do, declared independently of any UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .errors import ErrorCode, EZError


class Operation(str, Enum):
    CONVERT = "convert"  # re-encode/re-wrap into the target format
    EXTRACT_AUDIO = "extract_audio"  # drop video, keep/re-encode audio
    RASTERIZE = "rasterize"  # render pages/vector content to a raster image
    REPACK = "repack"  # same members, different archive container
    TRANSFORM = "transform"  # same format in and out: resize / compress / re-encode with new settings


class Fidelity(str, Enum):
    """Ordered from best to worst. The planner reports the worst step of a route."""

    BYTE_PRESERVING = "byte_preserving"
    LOSSLESS_REENCODE = "lossless_reencode"
    LOSSY = "lossy"
    LAYOUT_ALTERING = "layout_altering"
    SEMANTIC = "semantic"

    @property
    def rank(self) -> int:
        return list(Fidelity).index(self)


class Location(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True)
class OptionSpec:
    name: str
    type: str  # "int" | "float" | "bool" | "enum" | "string"
    default: Any
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] | None = None

    def coerce(self, value: Any) -> Any:
        def bad(msg: str) -> EZError:
            return EZError(ErrorCode.INVALID_OPTIONS, f"Option '{self.name}': {msg}", {"option": self.name})

        if self.type == "bool":
            if not isinstance(value, bool):
                raise bad("must be true or false")
            return value
        if self.type in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise bad("must be a number")
            if self.type == "int":
                if isinstance(value, float) and not value.is_integer():
                    raise bad("must be a whole number")
                value = int(value)
            else:
                value = float(value)
            if self.minimum is not None and value < self.minimum:
                raise bad(f"must be at least {self.minimum:g}")
            if self.maximum is not None and value > self.maximum:
                raise bad(f"must be at most {self.maximum:g}")
            return value
        if self.type == "enum":
            if value not in (self.choices or ()):
                raise bad(f"must be one of {', '.join(map(str, self.choices or ()))}")
            return value
        if self.type == "string":
            if not isinstance(value, str) or len(value) > 256:
                raise bad("must be a string of at most 256 characters")
            return value
        raise bad("unsupported option type")  # pragma: no cover

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "description": self.description,
        }
        if self.minimum is not None:
            d["minimum"] = self.minimum
        if self.maximum is not None:
            d["maximum"] = self.maximum
        if self.choices is not None:
            d["choices"] = list(self.choices)
        return d


def validate_options(specs: tuple[OptionSpec, ...], options: dict[str, Any] | None) -> dict[str, Any]:
    """Reject unknown options, coerce/range-check known ones, fill defaults."""
    options = options or {}
    known = {s.name: s for s in specs}
    unknown = sorted(set(options) - set(known))
    if unknown:
        raise EZError(
            ErrorCode.INVALID_OPTIONS,
            f"Unknown option(s) for this conversion: {', '.join(unknown)}.",
            {"unknown": unknown, "allowed": sorted(known)},
        )
    return {n: (s.coerce(options[n]) if n in options else s.default) for n, s in known.items()}


@dataclass(frozen=True)
class LicenseInfo:
    engine: str
    spdx: str
    redistribution_note: str = ""


@dataclass(frozen=True)
class ResourceHint:
    cpu_heavy: bool = False
    memory_factor: float = 4.0  # estimated peak RSS ~= factor * input size
    memory_floor_mb: int = 128
    seconds_per_mb: float = 0.5  # rough, used for UX estimates only
    indeterminate_progress: bool = True  # engine cannot report fractional progress
    max_input_mb: int | None = None  # reject larger inputs at planning time


@dataclass(frozen=True)
class Capability:
    id: str
    engine_id: str
    source: str
    target: str
    operation: Operation
    fidelity: Fidelity
    summary: str
    options: tuple[OptionSpec, ...] = ()
    environments: frozenset[Location] = frozenset({Location.LOCAL, Location.REMOTE})
    requires: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    license: LicenseInfo | None = None
    resources: ResourceHint = field(default_factory=ResourceHint)
    # Only capabilities flagged composable may serve as a step in a multi-step route.
    composable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "engine_id": self.engine_id,
            "source": self.source,
            "target": self.target,
            "operation": self.operation.value,
            "fidelity": self.fidelity.value,
            "summary": self.summary,
            "options": [o.to_dict() for o in self.options],
            "environments": sorted(e.value for e in self.environments),
            "requires": list(self.requires),
            "warnings": list(self.warnings),
            "license": {
                "engine": self.license.engine,
                "spdx": self.license.spdx,
                "redistribution_note": self.license.redistribution_note,
            }
            if self.license
            else None,
            "progress_reporting": "indeterminate" if self.resources.indeterminate_progress else "fractional",
        }
