"""Capability registry.

Adapters register themselves; a failing or absent engine never prevents the others from loading.
Only capabilities whose dependencies are present are *active* (advertised and plannable);
inactive ones remain listed with a reason so clients can explain what is unavailable here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from ..domain.capability import Capability, Location
from ..domain.formats import FORMATS
from ..engines.base import Availability, EngineAdapter

log = logging.getLogger("ezconverter.registry")


@dataclass
class RegisteredCapability:
    capability: Capability
    adapter: EngineAdapter
    availability: Availability


class CapabilityRegistry:
    def __init__(self, location: Location = Location.LOCAL):
        self.location = location
        self._adapters: dict[str, EngineAdapter] = {}
        self._caps: dict[str, RegisteredCapability] = {}
        self.load_errors: dict[str, str] = {}
        # When set, only these capability ids are advertised and plannable (conformance-verified set).
        self.restrict_to: set[str] | None = None

    # ------------------------------------------------------------------ registration

    def register(self, adapter: EngineAdapter) -> None:
        try:
            caps = adapter.capabilities()
            entries = []
            for cap in caps:
                if cap.source not in FORMATS or cap.target not in FORMATS:
                    raise ValueError(f"{cap.id}: unknown format id")
                if cap.id in self._caps:
                    raise ValueError(f"duplicate capability id {cap.id}")
                try:
                    av = adapter.availability(cap)
                except Exception as exc:  # a broken probe must not hide other capabilities
                    av = Availability(False, f"availability check failed: {type(exc).__name__}")
                entries.append(RegisteredCapability(cap, adapter, av))
        except Exception as exc:
            log.warning("engine %s failed to load: %s", getattr(adapter, "engine_id", "?"), exc)
            self.load_errors[getattr(adapter, "engine_id", repr(adapter))] = f"{type(exc).__name__}: {exc}"
            return
        self._adapters[adapter.engine_id] = adapter
        for e in entries:
            self._caps[e.capability.id] = e

    # ------------------------------------------------------------------ queries

    def get(self, capability_id: str) -> RegisteredCapability | None:
        return self._caps.get(capability_id)

    def adapter(self, engine_id: str) -> EngineAdapter | None:
        return self._adapters.get(engine_id)

    def all(self) -> list[RegisteredCapability]:
        return list(self._caps.values())

    def is_active(self, e: RegisteredCapability) -> bool:
        return (
            e.availability.ok
            and self.location in e.capability.environments
            and (self.restrict_to is None or e.capability.id in self.restrict_to)
        )

    def active(self) -> list[RegisteredCapability]:
        return [e for e in self._caps.values() if self.is_active(e)]

    def inactive(self) -> list[RegisteredCapability]:
        return [e for e in self._caps.values() if not self.is_active(e)]

    def inactive_reason(self, e: RegisteredCapability) -> str:
        if not e.availability.ok:
            return e.availability.reason or "engine unavailable"
        if self.location not in e.capability.environments:
            return f"not available on a {self.location.value} runtime"
        return "not yet verified by the conformance suite"

    def active_for_source(self, source: str) -> list[RegisteredCapability]:
        return [e for e in self.active() if e.capability.source == source]

    def targets_for(self, source: str) -> list[str]:
        """Direct targets only (single capability). Multi-step reachability comes from the planner."""
        return sorted({e.capability.target for e in self.active_for_source(source)})

    def engines(self) -> list[dict]:
        out = []
        for eid, ad in self._adapters.items():
            mine = [e for e in self._caps.values() if e.adapter is ad]
            ok = [e for e in mine if e.availability.ok]
            ver = next((e.availability.version for e in ok if e.availability.version), None)
            reason = None if ok else (mine[0].availability.reason if mine else "no capabilities")
            out.append(
                {
                    "engine_id": eid,
                    "available": bool(ok),
                    "version": ver,
                    "reason": reason,
                    "capabilities_total": len(mine),
                    "capabilities_active": len(ok),
                }
            )
        for eid, err in self.load_errors.items():
            out.append(
                {
                    "engine_id": eid,
                    "available": False,
                    "version": None,
                    "reason": err,
                    "capabilities_total": 0,
                    "capabilities_active": 0,
                }
            )
        return out


def default_registry(
    location: Location = Location.LOCAL, adapters: Iterable[EngineAdapter] | None = None
) -> CapabilityRegistry:
    reg = CapabilityRegistry(location)
    if adapters is None:
        adapters = _builtin_adapters()
    for ad in adapters:
        reg.register(ad)
    return reg


def _builtin_adapters() -> list[EngineAdapter]:
    """Each engine is imported independently so one broken optional dependency cannot block the rest."""
    import importlib

    out: list[EngineAdapter] = []
    for mod, cls in (
        ("images", "PillowEngine"),
        ("media", "FFmpegEngine"),
        ("documents", "LibreOfficeEngine"),
        ("documents", "PandocEngine"),
        ("documents", "PdfEngine"),
        ("archives", "ArchiveEngine"),
        ("structured", "StructuredEngine"),
    ):
        try:
            out.append(getattr(importlib.import_module(f"ezconverter.engines.{mod}"), cls)())
        except Exception as exc:
            log.warning("could not import engine %s.%s: %s", mod, cls, exc)
    out.extend(_plugin_adapters())
    return out


def _plugin_adapters() -> list[EngineAdapter]:
    """Operator-configured plugin engines: EZ_EXTRA_ENGINES="pkg.module:Class,/abs/path/file.py:Class".

    The variable is trusted configuration (it is code the operator chose to run), never user input.
    A plugin that fails to import is logged and skipped; it cannot prevent other engines from loading.
    """
    import importlib
    import importlib.util
    import os

    out: list[EngineAdapter] = []
    for spec in filter(None, (s.strip() for s in os.environ.get("EZ_EXTRA_ENGINES", "").split(","))):
        try:
            target, _, cls = spec.rpartition(":")
            if target.endswith(".py"):
                mod_spec = importlib.util.spec_from_file_location(f"ez_plugin_{abs(hash(target))}", target)
                assert mod_spec and mod_spec.loader
                mod = importlib.util.module_from_spec(mod_spec)
                mod_spec.loader.exec_module(mod)
            else:
                mod = importlib.import_module(target)
            out.append(getattr(mod, cls)())
        except Exception as exc:
            log.warning("could not load plugin engine %s: %s", spec, exc)
    return out
