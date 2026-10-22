"""Public API contract (Pydantic). OpenAPI is generated from these models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobStateLit = Literal[
    "CREATED", "VALIDATING", "QUEUED", "RUNNING", "VERIFYING", "SUCCEEDED", "FAILED", "CANCELLED"
]
PolicyLit = Literal["AUTO", "LOCAL_ONLY", "REMOTE_ALLOWED"]
FidelityLit = Literal["byte_preserving", "lossless_reencode", "lossy", "layout_altering", "semantic"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code")
    message: str = Field(description="Safe, user-facing explanation")
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    location: Literal["local", "remote"] = Field(
        description="Whether this runtime processes files on the user's device or remotely"
    )
    api_version: str = "v1"
    conformance: Literal["verified", "unverified"]
    network_isolation: Literal["active", "unavailable", "disabled"] = Field(
        description="Whether conversion workers run in an empty network namespace on this runtime"
    )
    retention_hours: float = Field(
        description="How long uploads and results are kept before automatic deletion"
    )
    max_upload_mb: int
    max_batch_files: int


class FormatOut(BaseModel):
    id: str
    name: str
    family: str
    mime: str
    extensions: list[str]


class OptionOut(BaseModel):
    name: str
    type: Literal["int", "float", "bool", "enum", "string"]
    default: Any = None
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: list[Any] | None = None


class LicenseOut(BaseModel):
    engine: str
    spdx: str
    redistribution_note: str = ""


class CapabilityOut(BaseModel):
    id: str
    engine_id: str
    source: str
    target: str
    operation: str
    fidelity: FidelityLit
    summary: str
    options: list[OptionOut]
    environments: list[str]
    requires: list[str]
    warnings: list[str]
    license: LicenseOut | None = None
    progress_reporting: Literal["fractional", "indeterminate"]
    composable: bool = False
    available: bool = True
    verified: bool = False
    unavailable_reason: str | None = None


class EngineOut(BaseModel):
    engine_id: str
    available: bool
    version: str | None = None
    reason: str | None = None
    capabilities_total: int
    capabilities_active: int


class CapabilitiesOut(BaseModel):
    location: Literal["local", "remote"]
    formats: list[FormatOut]
    capabilities: list[CapabilityOut]
    engines: list[EngineOut]


class DetectionOut(BaseModel):
    format: str | None
    confidence: Literal["high", "medium", "low"]
    method: str
    mime: str | None = None
    extension_format: str | None = None
    extension_mismatch: bool = False
    damaged: bool = False
    rejected_reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class FileOut(BaseModel):
    id: str
    original_name: str
    size: int
    sha256: str
    format: str | None
    detection: DetectionOut
    created_at: float
    expires_at: float
    eligible: bool = Field(description="True when the file was recognised and is safe to convert")
    ineligible_reason: str | None = None


class TargetOut(BaseModel):
    target: str
    steps: int
    transform: bool = Field(
        default=False, description="True when the target equals the source format (resize/compress/re-encode)"
    )
    fidelity: FidelityLit
    engines: list[str]


class TargetsOut(BaseModel):
    source: str | None
    targets: list[TargetOut]


class PlanRequest(_M):
    file_id: str
    target: str
    options: dict[str, Any] = Field(default_factory=dict)
    execution_policy: PolicyLit = "LOCAL_ONLY"


class PlanStepOut(BaseModel):
    capability_id: str
    engine_id: str
    source: str
    target: str
    operation: str
    fidelity: FidelityLit
    warnings: list[str]
    options: dict[str, Any]


class EstimateOut(BaseModel):
    memory_mb: int
    seconds: float
    rough: bool
    progress: Literal["fractional", "indeterminate"]


class PlanOut(BaseModel):
    source: str
    target: str
    steps: list[PlanStepOut]
    fidelity: FidelityLit
    warnings: list[str]
    options: dict[str, Any]
    estimate: EstimateOut
    location: Literal["local", "remote"]
    multi_step: bool


class ConversionRequest(PlanRequest):
    pass


class JobErrorOut(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool


class JobOut(BaseModel):
    id: str
    batch_id: str | None = None
    file_id: str
    original_name: str
    source_format: str
    target_format: str
    state: JobStateLit
    progress: float | None = Field(
        default=None, description="0..1 when the engine can measure it, otherwise null (indeterminate)"
    )
    phase: str | None = None
    attempts: int
    max_attempts: int
    error: JobErrorOut | None = None
    warnings: list[str] = Field(default_factory=list)
    plan: PlanOut | None = None
    cancel_requested: bool = False
    artifact_id: str | None = None
    can_retry: bool = False
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None
    expires_at: float


class CheckOut(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class VerificationOut(BaseModel):
    ok: bool
    checks: list[CheckOut]
    size: int
    sha256: str
    detected_format: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class JobResultOut(BaseModel):
    job_id: str
    artifact_id: str
    name: str
    size: int
    sha256: str
    mime: str
    fidelity: FidelityLit
    warnings: list[str]
    verification: VerificationOut
    provenance: dict[str, Any]
    download_path: str


class BatchItem(_M):
    file_id: str
    target: str
    options: dict[str, Any] = Field(default_factory=dict)
    execution_policy: PolicyLit | None = None


class BatchRequest(_M):
    items: list[BatchItem] = Field(min_length=1)
    execution_policy: PolicyLit = "LOCAL_ONLY"


class BatchOut(BaseModel):
    id: str
    state: Literal["QUEUED", "RUNNING", "COMPLETED"]
    created_at: float
    expires_at: float
    total: int
    counts: dict[str, int]
    progress: float
    progress_indeterminate: bool
    archive_available: bool
    jobs: list[JobOut]


class BatchResultOut(BaseModel):
    batch_id: str
    succeeded: list[JobResultOut]
    failed: list[JobOut]
    cancelled: list[JobOut]
    archive_available: bool
    archive_path: str | None = None


class SignedLink(BaseModel):
    url: str
    expires_at: float
