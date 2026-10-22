"""Conformance manifest: the set of capabilities that passed the real-engine conformance suite."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("verified_capabilities.json")


def load_verified(path: Path | None = None) -> set[str] | None:
    p = path or DEFAULT_PATH
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return set(data["verified"])


def write_verified(ids: list[str], versions: dict[str, str], path: Path | None = None) -> None:
    p = path or DEFAULT_PATH
    p.write_text(json.dumps({"verified": sorted(ids), "engine_versions": versions}, indent=1) + "\n")
