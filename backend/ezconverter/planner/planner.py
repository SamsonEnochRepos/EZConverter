"""Deterministic conversion planner.

Routes are built only from registered, active capabilities. A multi-step route is considered
only when every step is flagged `composable`, and never longer than MAX_STEPS. Among candidate
routes the planner prefers fewer steps, then the better worst-step fidelity, then lexical order,
so identical inputs always produce the same plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.capability import Capability, Fidelity, Location
from ..domain.errors import ErrorCode, EZError
from ..domain.formats import FORMATS, Family
from ..domain.policy import ExecutionPolicy, enforce_location
from ..engines.base import InputInfo
from ..registry import CapabilityRegistry, RegisteredCapability

MAX_STEPS = 3


@dataclass
class PlanStep:
    capability_id: str
    engine_id: str
    source: str
    target: str
    operation: str
    fidelity: Fidelity
    warnings: list[str]
    options: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "engine_id": self.engine_id,
            "source": self.source,
            "target": self.target,
            "operation": self.operation,
            "fidelity": self.fidelity.value,
            "warnings": self.warnings,
            "options": self.options,
        }


@dataclass
class ConversionPlan:
    source: str
    target: str
    steps: list[PlanStep]
    fidelity: Fidelity
    warnings: list[str]
    options: dict[str, Any]
    estimate: dict[str, Any]
    location: Location

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "steps": [s.to_dict() for s in self.steps],
            "fidelity": self.fidelity.value,
            "warnings": self.warnings,
            "options": self.options,
            "estimate": self.estimate,
            "location": self.location.value,
            "multi_step": len(self.steps) > 1,
        }


KNOWN_GAPS: list[tuple[Any, str]] = [
    (
        lambda s, t: t == "svg" and FORMATS[s].family is Family.IMAGE,
        "Converting a raster image to SVG requires vector tracing, which only approximates the original "
        "and is not offered.",
    ),
    (
        lambda s, t: (
            s == "pdf"
            and t in ("docx", "doc", "odt", "pptx", "ppt", "odp", "xlsx", "xls", "ods", "html", "rtf", "md")
        ),
        "Rebuilding an editable document from a PDF requires layout reconstruction, which is unreliable and "
        "is not offered. You can extract the text (TXT) or render pages as images instead.",
    ),
    (
        lambda s, t: FORMATS[s].family in (Family.AUDIO, Family.VIDEO) and FORMATS[t].family is Family.IMAGE,
        "Extracting frames or waveforms as images is not supported yet.",
    ),
]


class Planner:
    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    # ------------------------------------------------------------------ reachability

    def _edges(self, policy: ExecutionPolicy) -> dict[str, list[RegisteredCapability]]:
        enforce_location(policy, self.registry.location)
        edges: dict[str, list[RegisteredCapability]] = {}
        for e in sorted(self.registry.active(), key=lambda e: e.capability.id):
            edges.setdefault(e.capability.source, []).append(e)
        return edges

    def routes(
        self, source: str, target: str, policy: ExecutionPolicy = ExecutionPolicy.LOCAL_ONLY
    ) -> list[list[RegisteredCapability]]:
        edges = self._edges(policy)
        if source == target:  # same-format transforms are only ever a single, explicit capability
            return [[e] for e in edges.get(source, []) if e.capability.target == source]
        found: list[list[RegisteredCapability]] = []

        def dfs(fmt: str, path: list[RegisteredCapability], seen: set[str]) -> None:
            for e in edges.get(fmt, []):
                cap = e.capability
                if cap.target in seen:
                    continue
                new = path + [e]
                if len(new) > 1 and not all(x.capability.composable for x in new):
                    continue
                if cap.target == target:
                    found.append(new)
                    continue
                if len(new) < MAX_STEPS and cap.composable:
                    dfs(cap.target, new, seen | {cap.target})

        dfs(source, [], {source})
        found.sort(
            key=lambda r: (len(r), max(x.capability.fidelity.rank for x in r), [x.capability.id for x in r])
        )
        return found

    def reachable_targets(
        self, source: str, policy: ExecutionPolicy = ExecutionPolicy.LOCAL_ONLY
    ) -> list[dict[str, Any]]:
        out = []
        for t in sorted(FORMATS):
            routes = self.routes(source, t, policy)
            if routes:
                r = routes[0]
                out.append(
                    {
                        "target": t,
                        "steps": len(r),
                        "transform": t == source,
                        "fidelity": max((x.capability.fidelity for x in r), key=lambda f: f.rank).value,
                        "engines": [x.capability.engine_id for x in r],
                    }
                )
        return out

    # ------------------------------------------------------------------ planning

    def plan(
        self,
        info: InputInfo,
        target: str,
        options: dict[str, Any] | None = None,
        policy: ExecutionPolicy = ExecutionPolicy.LOCAL_ONLY,
    ) -> ConversionPlan:
        enforce_location(policy, self.registry.location)
        if target not in FORMATS:
            raise EZError(
                ErrorCode.UNSUPPORTED_CONVERSION,
                f"'{target}' is not a recognised target format.",
                {"target": target},
            )
        source = info.format
        routes = self.routes(source, target, policy)
        if source == target:
            same = {"source": source, "target": target}
            if not routes:
                raise EZError(
                    ErrorCode.UNSUPPORTED_CONVERSION,
                    f"The file is already {FORMATS[target].name}. Choose a different target format.",
                    same,
                )
            entry = routes[0][0]
            opts = entry.adapter.validate_options(entry.capability, options)
            defaults = {o.name: o.default for o in entry.capability.options}
            if all(
                opts[k] == defaults[k]
                for k in opts
                if k
                in (
                    "max_width",
                    "max_height",
                    "quality",
                    "fps",
                    "crf",
                    "bitrate_kbps",
                    "sample_rate",
                    "channels",
                    "video_bitrate_kbps",
                    "audio_bitrate_kbps",
                    "bit_depth",
                    "lossless",
                )
            ):
                raise EZError(
                    ErrorCode.UNSUPPORTED_CONVERSION,
                    f"The file is already {FORMATS[target].name}. To keep the format, set an option such as a smaller "
                    "size or lower quality; otherwise choose a different target format.",
                    same,
                )
        if not routes:
            raise self._no_route(source, target)
        route = routes[0]

        last = route[-1]
        validated = last.adapter.validate_options(last.capability, options)
        # Options always address the final step; earlier steps of a multi-step route use defaults.

        steps: list[PlanStep] = []
        warnings: list[str] = []
        cur = info
        for i, entry in enumerate(route):
            cap, adapter = entry.capability, entry.adapter
            opts = validated if entry is last else adapter.validate_options(cap, None)
            self._check_size(cap, info if i == 0 else cur)
            assessment = adapter.assess(cap, cur, opts)
            steps.append(
                PlanStep(
                    cap.id,
                    cap.engine_id,
                    cap.source,
                    cap.target,
                    cap.operation.value,
                    assessment.fidelity,
                    assessment.warnings,
                    opts,
                )
            )
            for w in assessment.warnings:
                warnings.append(w if len(route) == 1 else f"Step {i + 1} ({cap.source}→{cap.target}): {w}")
            cur = InputInfo(
                path=Path("."), filename="intermediate", format=cap.target, size=cur.size, details={}
            )
        if len(route) > 1:
            chain = " → ".join([route[0].capability.source] + [e.capability.target for e in route])
            warnings.insert(
                0,
                f"This conversion runs in {len(route)} steps ({chain}); quality is limited by the weakest step.",
            )
        fidelity = max((s.fidelity for s in steps), key=lambda f: f.rank)
        return ConversionPlan(
            source,
            target,
            steps,
            fidelity,
            list(dict.fromkeys(warnings)),
            validated,
            self._estimate(route, info),
            self.registry.location,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _check_size(cap: Capability, info: InputInfo) -> None:
        lim = cap.resources.max_input_mb
        if lim and info.size > lim * 1024 * 1024:
            raise EZError(
                ErrorCode.FILE_TOO_LARGE,
                f"This conversion supports files up to {lim} MB; this file is {info.size / 1048576:.0f} MB.",
                {"limit_mb": lim},
            )

    @staticmethod
    def _estimate(route: list[RegisteredCapability], info: InputInfo) -> dict[str, Any]:
        mb = max(info.size / 1048576, 0.01)
        mem = max(
            max(e.capability.resources.memory_floor_mb, int(e.capability.resources.memory_factor * mb))
            for e in route
        )
        media_dur = info.details.get("media", {}).get("duration_seconds")
        secs = 0.0
        for e in route:
            r = e.capability.resources
            secs += (
                (media_dur * (0.5 if r.cpu_heavy else 0.05))
                if media_dur and e.capability.engine_id == "ffmpeg"
                else mb * r.seconds_per_mb
            )
            secs += 3 if e.capability.engine_id == "libreoffice" else 0.3  # start-up cost
        return {
            "memory_mb": mem,
            "seconds": round(secs, 1),
            "rough": True,
            "progress": "indeterminate"
            if any(e.capability.resources.indeterminate_progress for e in route)
            else "fractional",
        }

    def _no_route(self, source: str, target: str) -> EZError:
        sf, tf = FORMATS[source], FORMATS[target]
        for pred, msg in KNOWN_GAPS:
            if pred(source, target):
                return EZError(ErrorCode.UNSUPPORTED_CONVERSION, msg, self._suggest(source, target))
        inactive = [
            e
            for e in self.registry.all()
            if e.capability.source == source
            and e.capability.target == target
            and not self.registry.is_active(e)
        ]
        if inactive:
            e = inactive[0]
            reason = self.registry.inactive_reason(e)
            return EZError(
                ErrorCode.ENGINE_UNAVAILABLE,
                f"{sf.name} → {tf.name} is not available on this runtime right now: {reason}.",
                {**self._suggest(source, target), "engine": e.capability.engine_id},
            )
        if sf.family is not tf.family and not {sf.family, tf.family} <= {
            Family.DOCUMENT,
            Family.SPREADSHEET,
            Family.PRESENTATION,
            Family.DATA,
            Family.IMAGE,
        }:
            msg = f"A {sf.family.value} file cannot be converted into {tf.name} ({tf.family.value})."
        else:
            msg = f"Converting {sf.name} to {tf.name} is not supported."
        return EZError(ErrorCode.UNSUPPORTED_CONVERSION, msg, self._suggest(source, target))

    def _suggest(self, source: str, target: str) -> dict[str, Any]:
        try:
            avail = [t["target"] for t in self.reachable_targets(source, ExecutionPolicy.LOCAL_ONLY)]
        except EZError:
            avail = []
        return {"source": source, "target": target, "available_targets": avail}
