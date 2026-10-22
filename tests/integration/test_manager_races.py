"""Manager shutdown and cancel races, driven with a fake worker so the interleavings are deterministic."""

import hashlib
import shutil
import threading
import time
import uuid

import pytest

from ezconverter.api.app import build_runtime
from ezconverter.domain.errors import ErrorCode, EZError
from ezconverter.domain.policy import ExecutionPolicy
from ezconverter.orchestration import manager as manager_mod


@pytest.fixture()
def rt_job(settings):
    """A runtime with one QUEUED job and no dispatcher thread; the test claims and runs jobs itself."""
    from fixtures.generate import fixture

    rt = build_runtime(settings)
    src = fixture("json")
    fid = uuid.uuid4().hex
    shutil.copyfile(src, rt.storage.upload_path(fid))
    rt.manager.add_file(
        "alice", fid, "data.json", src.stat().st_size, hashlib.sha256(src.read_bytes()).hexdigest()
    )
    job = rt.manager.create_job("alice", fid, "yaml", {}, ExecutionPolicy.LOCAL_ONLY)
    yield rt, job
    rt.store.close()


def _claim_and_run(m):
    with m._cv:
        m._fill_slots()


def _wait_state(store, jid, states, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        j = store.get_job(jid)
        if j["state"] in states:
            return j
        time.sleep(0.02)
    raise AssertionError(f"job stuck in {store.get_job(jid)['state']}")


def test_shutdown_requeues_running_job_instead_of_cancelling(rt_job, monkeypatch):
    rt, job = rt_job
    m, store = rt.manager, rt.store
    started = threading.Event()

    def fake_worker(request, workdir, limits, cancel=None, on_event=None):
        started.set()
        assert cancel.wait(20), "stop() never signalled the worker"
        raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")  # what run_worker does on kill

    monkeypatch.setattr(manager_mod, "run_worker", fake_worker)
    _claim_and_run(m)
    assert started.wait(10)
    assert store.get_job(job["id"])["state"] == "RUNNING"

    m.stop()  # normal shutdown while the job is in flight

    j = store.get_job(job["id"])
    assert j["state"] == "QUEUED", j["state"]  # recoverable, not a user cancellation
    assert not j["cancel_requested"] and j["error"] is None
    assert j["attempts"] == 0  # a restart is not the job's fault: the attempt is refunded
    m.recover()  # nothing left in flight; the job stays queued for the next start
    assert store.get_job(job["id"])["state"] == "QUEUED"


def test_user_cancel_during_shutdown_still_cancels(rt_job, monkeypatch):
    rt, job = rt_job
    m, store = rt.manager, rt.store
    started = threading.Event()

    def fake_worker(request, workdir, limits, cancel=None, on_event=None):
        started.set()
        assert cancel.wait(20)
        raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")

    monkeypatch.setattr(manager_mod, "run_worker", fake_worker)
    _claim_and_run(m)
    assert started.wait(10)
    store.update_job(job["id"], cancel_requested=1)  # the user asked first
    m.stop()
    assert store.get_job(job["id"])["state"] == "CANCELLED"


def test_job_claimed_while_stopping_is_interrupted_not_run_to_completion(rt_job, monkeypatch):
    rt, job = rt_job
    m, store = rt.manager, rt.store
    seen = {}

    def fake_worker(request, workdir, limits, cancel=None, on_event=None):
        seen["cancel_set"] = cancel.wait(5)
        raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")

    monkeypatch.setattr(manager_mod, "run_worker", fake_worker)
    m._stop.set()  # stop() has begun; its snapshot of running jobs is already taken
    _claim_and_run(m)
    m.stop()
    assert seen["cancel_set"] is True
    assert store.get_job(job["id"])["state"] == "QUEUED"


def test_cancel_between_claim_and_event_registration_is_not_lost(rt_job, monkeypatch):
    rt, job = rt_job
    m, store = rt.manager, rt.store
    seen = {}

    real_claim = store.claim

    def claim_then_cancel(job_id):
        stale = real_claim(job_id)  # RUNNING, cancel_requested == 0 in this snapshot
        m.cancel_job("alice", job_id)  # the API cancel lands before _run_job registers its event
        return stale

    def fake_worker(request, workdir, limits, cancel=None, on_event=None):
        seen["cancel_set"] = cancel.wait(1.5)
        if cancel.is_set():
            raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
        raise EZError(ErrorCode.CONVERSION_FAILED, "finished without ever being told to stop")

    monkeypatch.setattr(store, "claim", claim_then_cancel)
    monkeypatch.setattr(manager_mod, "run_worker", fake_worker)
    _claim_and_run(m)
    j = _wait_state(store, job["id"], {"SUCCEEDED", "FAILED", "CANCELLED"})
    m._pool.shutdown(wait=True)
    assert seen["cancel_set"] is True  # the worker was signalled, not left running to completion
    assert j["state"] == "CANCELLED" and j["cancel_requested"]
