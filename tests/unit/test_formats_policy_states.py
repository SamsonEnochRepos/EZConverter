import pytest

from ezconverter.domain.capability import (
    Location,
    OptionSpec,
    validate_options,
)
from ezconverter.domain.errors import RETRYABLE, ErrorCode, EZError
from ezconverter.domain.formats import FORMATS, format_from_extension
from ezconverter.domain.policy import ExecutionPolicy, enforce_location
from ezconverter.domain.states import JobState, assert_transition, can_transition


def test_format_catalog_is_consistent():
    for fid, f in FORMATS.items():
        assert f.id == fid and f.mime and f.extensions
    assert format_from_extension("a.JPG") == "jpeg"
    assert format_from_extension("backup.tar.gz") == "tgz"
    assert format_from_extension("noext") is None
    assert format_from_extension("weird.xyz") is None


def test_option_validation_coerces_and_rejects():
    specs = (
        OptionSpec("q", "int", 80, "quality", 1, 100),
        OptionSpec("m", "enum", "a", "mode", choices=("a", "b")),
        OptionSpec("flag", "bool", False, "f"),
    )
    assert validate_options(specs, None) == {"q": 80, "m": "a", "flag": False}
    assert validate_options(specs, {"q": 10.0, "m": "b"})["q"] == 10
    for bad in (
        {"q": 0},
        {"q": 101},
        {"q": "50"},
        {"q": True},
        {"q": 1.5},
        {"m": "c"},
        {"flag": 1},
        {"nope": 1},
    ):
        with pytest.raises(EZError) as e:
            validate_options(specs, bad)
        assert e.value.code is ErrorCode.INVALID_OPTIONS


def test_unknown_option_error_lists_allowed():
    with pytest.raises(EZError) as e:
        validate_options((OptionSpec("q", "int", 1, "d"),), {"zzz": 1})
    assert e.value.details["allowed"] == ["q"]


def test_state_machine_allows_only_declared_transitions():
    ok = [
        ("CREATED", "VALIDATING"),
        ("VALIDATING", "QUEUED"),
        ("QUEUED", "RUNNING"),
        ("RUNNING", "VERIFYING"),
        ("VERIFYING", "SUCCEEDED"),
        ("RUNNING", "FAILED"),
        ("FAILED", "QUEUED"),
        ("RUNNING", "QUEUED"),
        ("QUEUED", "CANCELLED"),
    ]
    for a, b in ok:
        assert can_transition(JobState(a), JobState(b)), (a, b)
    bad = [
        ("SUCCEEDED", "QUEUED"),
        ("SUCCEEDED", "FAILED"),
        ("CANCELLED", "QUEUED"),
        ("QUEUED", "SUCCEEDED"),
        ("CREATED", "RUNNING"),
        ("RUNNING", "SUCCEEDED"),
        ("FAILED", "SUCCEEDED"),
        ("VERIFYING", "RUNNING"),
    ]
    for a, b in bad:
        assert not can_transition(JobState(a), JobState(b)), (a, b)
        with pytest.raises(EZError) as e:
            assert_transition(JobState(a), JobState(b))
        assert e.value.code is ErrorCode.INVALID_STATE


def test_terminal_states():
    assert {s for s in JobState if s.terminal} == {
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
    }


def test_policy_remote_runtime_rejects_local_only_and_auto():
    with pytest.raises(EZError) as e:
        enforce_location(ExecutionPolicy.LOCAL_ONLY, Location.REMOTE)
    assert e.value.code is ErrorCode.LOCAL_ONLY_VIOLATION
    with pytest.raises(EZError) as e:
        enforce_location(ExecutionPolicy.AUTO, Location.REMOTE)
    assert e.value.code is ErrorCode.REMOTE_CONSENT_REQUIRED
    enforce_location(ExecutionPolicy.REMOTE_ALLOWED, Location.REMOTE)
    for p in ExecutionPolicy:
        enforce_location(p, Location.LOCAL)


def test_error_retryability():
    assert ErrorCode.WORKER_LOST in RETRYABLE and ErrorCode.TIMEOUT in RETRYABLE
    for c in (
        ErrorCode.MALFORMED_INPUT,
        ErrorCode.UNSUPPORTED_CONVERSION,
        ErrorCode.OUTPUT_VALIDATION_FAILED,
        ErrorCode.UNSAFE_INPUT,
    ):
        assert c not in RETRYABLE
    assert EZError(ErrorCode.NOT_FOUND, "x").http_status == 404
