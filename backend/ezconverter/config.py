"""Runtime configuration from environment variables (prefix EZ_). No secrets have defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .domain.capability import Location


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = Path("./data")
    location: Location = Location.LOCAL
    auth_mode: str = "token"  # "token" | "none" (none only for trusted loopback use)
    api_keys: dict[str, str] = field(default_factory=dict)  # token -> user id
    max_upload_mb: int = 1024
    max_batch_files: int = 100
    max_concurrency: int = 2
    job_timeout_s: int = 900
    worker_memory_mb: int = 6144
    worker_cpu_s: int = 900
    worker_output_mb: int = 4096
    engine_threads: int = 2
    retention_hours: float = 24.0
    user_quota_mb: int = 4096
    rate_limit_per_min: int = 600
    max_attempts: int = 3
    allowed_origins: list[str] = field(default_factory=list)
    advertise: str = "verified"  # "verified" | "all"
    # "require": refuse to start when workers cannot be put in an empty network namespace;
    # "auto": isolate when possible and say so loudly when not; "off": never isolate (tests / trusted containers).
    network_isolation: str = "auto"
    sweep_interval_s: float = 60.0
    conformance_manifest: Path | None = None

    @property
    def isolate_network(self) -> bool:
        return self.network_isolation != "off"

    @classmethod
    def from_env(cls) -> Settings:
        keys: dict[str, str] = {}
        for pair in filter(None, (p.strip() for p in os.environ.get("EZ_API_KEYS", "").split(","))):
            tok, _, user = pair.partition(":")
            if tok and user:
                keys[tok] = user
        manifest = os.environ.get("EZ_CONFORMANCE_MANIFEST")
        return cls(
            data_dir=Path(os.environ.get("EZ_DATA_DIR", "./data")),
            location=Location(os.environ.get("EZ_LOCATION", "local")),
            auth_mode=os.environ.get("EZ_AUTH_MODE", "token"),
            api_keys=keys,
            max_upload_mb=_int("EZ_MAX_UPLOAD_MB", 1024),
            max_batch_files=_int("EZ_MAX_BATCH_FILES", 100),
            max_concurrency=max(1, _int("EZ_MAX_CONCURRENCY", 2)),
            job_timeout_s=_int("EZ_JOB_TIMEOUT_S", 900),
            worker_memory_mb=_int("EZ_WORKER_MEMORY_MB", 6144),
            worker_cpu_s=_int("EZ_WORKER_CPU_S", 900),
            worker_output_mb=_int("EZ_WORKER_OUTPUT_MB", 4096),
            engine_threads=_int("EZ_ENGINE_THREADS", 2),
            retention_hours=float(os.environ.get("EZ_RETENTION_HOURS", 24)),
            user_quota_mb=_int("EZ_USER_QUOTA_MB", 4096),
            rate_limit_per_min=_int("EZ_RATE_LIMIT_PER_MIN", 600),
            max_attempts=max(1, _int("EZ_MAX_ATTEMPTS", 3)),
            allowed_origins=[o for o in os.environ.get("EZ_ALLOWED_ORIGINS", "").split(",") if o],
            advertise=os.environ.get("EZ_ADVERTISE", "verified"),
            network_isolation=os.environ.get("EZ_NETWORK_ISOLATION", "auto"),
            sweep_interval_s=float(os.environ.get("EZ_SWEEP_INTERVAL_S", 60)),
            conformance_manifest=Path(manifest) if manifest else None,
        )
