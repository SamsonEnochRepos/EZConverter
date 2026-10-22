"""Job lifecycle with enforced transitions."""

from __future__ import annotations

from enum import Enum

from .errors import ErrorCode, EZError


class JobState(str, Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL


TERMINAL = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})

_ALLOWED: dict[JobState, frozenset[JobState]] = {
    JobState.CREATED: frozenset({JobState.VALIDATING, JobState.CANCELLED, JobState.FAILED}),
    JobState.VALIDATING: frozenset({JobState.QUEUED, JobState.FAILED, JobState.CANCELLED}),
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED, JobState.FAILED}),
    # RUNNING -> QUEUED: bounded automatic retry after a lost worker.
    JobState.RUNNING: frozenset({JobState.VERIFYING, JobState.FAILED, JobState.CANCELLED, JobState.QUEUED}),
    JobState.VERIFYING: frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.QUEUED}),
    JobState.SUCCEEDED: frozenset(),
    JobState.FAILED: frozenset({JobState.QUEUED}),  # explicit, bounded retry only
    JobState.CANCELLED: frozenset(),
}


def can_transition(src: JobState, dst: JobState) -> bool:
    return dst in _ALLOWED[src]


def assert_transition(src: JobState, dst: JobState) -> None:
    if not can_transition(src, dst):
        raise EZError(
            ErrorCode.INVALID_STATE,
            f"Cannot move a job from {src.value} to {dst.value}.",
            {"from": src.value, "to": dst.value},
        )
