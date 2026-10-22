"""Execution policy. Pure logic, no I/O."""

from __future__ import annotations

from enum import Enum

from .capability import Capability, Location
from .errors import ErrorCode, EZError


class ExecutionPolicy(str, Enum):
    AUTO = "AUTO"
    LOCAL_ONLY = "LOCAL_ONLY"
    REMOTE_ALLOWED = "REMOTE_ALLOWED"


def enforce_location(policy: ExecutionPolicy, runtime: Location) -> None:
    """Called by a runtime before it accepts any bytes or job.

    A remote runtime never accepts LOCAL_ONLY, and never accepts AUTO: AUTO is resolved to a
    concrete policy by the client router after the user has consented to remote processing.
    """
    if runtime is Location.REMOTE:
        if policy is ExecutionPolicy.LOCAL_ONLY:
            raise EZError(
                ErrorCode.LOCAL_ONLY_VIOLATION,
                "This request is restricted to local processing, but this server is a remote runtime. "
                "Nothing was uploaded.",
            )
        if policy is ExecutionPolicy.AUTO:
            raise EZError(
                ErrorCode.REMOTE_CONSENT_REQUIRED,
                "Remote processing needs an explicit REMOTE_ALLOWED policy. "
                "AUTO must be resolved by the client before upload.",
            )


def capability_allowed(cap: Capability, policy: ExecutionPolicy, runtime: Location) -> bool:
    enforce_location(policy, runtime)
    return runtime in cap.environments
