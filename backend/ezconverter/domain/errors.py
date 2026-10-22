"""Error taxonomy. Every error carries a machine-readable code and a safe message."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    # request / auth
    INVALID_REQUEST = "invalid_request"
    UNAUTHENTICATED = "unauthenticated"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INVALID_STATE = "invalid_state"
    # policy
    LOCAL_ONLY_VIOLATION = "local_only_violation"
    REMOTE_CONSENT_REQUIRED = "remote_consent_required"
    # input
    FILE_TOO_LARGE = "file_too_large"
    QUOTA_EXCEEDED = "quota_exceeded"
    UNSUPPORTED_INPUT = "unsupported_input"
    MALFORMED_INPUT = "malformed_input"
    UNSAFE_INPUT = "unsafe_input"
    # planning
    UNSUPPORTED_CONVERSION = "unsupported_conversion"
    INVALID_OPTIONS = "invalid_options"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    # execution
    CONVERSION_FAILED = "conversion_failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"
    WORKER_LOST = "worker_lost"
    CANCELLED = "cancelled"
    INTERNAL = "internal_error"


# Which execution failures are worth an explicit retry. Input problems never are.
RETRYABLE: frozenset[ErrorCode] = frozenset(
    {ErrorCode.WORKER_LOST, ErrorCode.TIMEOUT, ErrorCode.INTERNAL, ErrorCode.RESOURCE_LIMIT}
)

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.LOCAL_ONLY_VIOLATION: 409,
    ErrorCode.REMOTE_CONSENT_REQUIRED: 409,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.QUOTA_EXCEEDED: 413,
    ErrorCode.UNSUPPORTED_INPUT: 415,
    ErrorCode.MALFORMED_INPUT: 422,
    ErrorCode.UNSAFE_INPUT: 422,
    ErrorCode.UNSUPPORTED_CONVERSION: 422,
    ErrorCode.INVALID_OPTIONS: 422,
    ErrorCode.ENGINE_UNAVAILABLE: 503,
}


class EZError(Exception):
    """Base error. `message` must be safe to show to end users (no paths, no secrets)."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)
