"""Orchestrator behaviour under failure, using TEST-ONLY engines in real sandboxed worker processes."""

import socket
import threading
import time

import pytest
from conftest import make_client, submit, upload, wait_job, wait_state

from ezconverter.api.app import build_runtime
from ezconverter.domain.policy import ExecutionPolicy


def test_cancel_running_job_kills_worker_and_leaves_no_output(te_client, fx):
    c = te_client
    jid = submit(c, fx("rtf"), "xml", {"sleep_seconds": 120})
    wait_state(c, jid, {"RUNNING"})
    time.sleep(0.5)
    t = time.time()
    assert c.post(f"/v1/jobs/{jid}/cancel").status_code == 200
    j = wait_state(c, jid, {"CANCELLED", "SUCCEEDED", "FAILED"}, 20)
    assert j["state"] == "CANCELLED" and time.time() - t < 8 and j["artifact_id"] is None
    assert j["error"]["code"] == "cancelled" and not j["can_retry"]
    assert c.get(f"/v1/jobs/{jid}/result").status_code == 409
    rt = c.app.state.rt
    assert not (rt.storage.root / "jobs" / jid / "attempt1").exists()  # workdir cleaned
    assert rt.store.q("SELECT COUNT(*) n FROM artifacts")[0]["n"] == 0


def test_progress_is_real_and_monotonic(te_client, fx):
    c = te_client
    jid = submit(c, fx("rtf"), "xml", {"sleep_seconds": 4})
    seen = []
    while True:
        j = c.get(f"/v1/jobs/{jid}").json()
        if j["progress"] is not None and j["state"] != "SUCCEEDED":
            seen.append(j["progress"])
        if j["state"] in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(0.2)
    assert j["state"] == "SUCCEEDED" and len(seen) >= 3
    assert seen == sorted(seen) and 0 <= seen[0] < 0.9 and j["progress"] == 1.0


def test_cancel_queued_job_is_immediate(settings, monkeypatch, fx):
    monkeypatch.setenv(
        "EZ_EXTRA_ENGINES",
        f"{__import__('conftest').HELPERS / 'test_engines.py'}:TestEngines",
    )
    settings.advertise = "all"
    settings.max_concurrency = 1
    with make_client(settings) as c:
        c.headers["Authorization"] = "Bearer tok-alice"
        first = submit(c, fx("rtf"), "xml", {"sleep_seconds": 30})
        wait_state(c, first, {"RUNNING"})
        second = submit(c, fx("rtf"), "xml", {"sleep_seconds": 30})
        assert c.get(f"/v1/jobs/{second}").json()["state"] == "QUEUED"
        r = c.post(f"/v1/jobs/{second}/cancel").json()
        assert r["state"] == "CANCELLED"
        c.post(f"/v1/jobs/{first}/cancel")
        wait_state(c, first, {"CANCELLED"})


def test_timeout_is_enforced_and_retry_is_bounded(settings, monkeypatch, fx):
    monkeypatch.setenv(
        "EZ_EXTRA_ENGINES",
        f"{__import__('conftest').HELPERS / 'test_engines.py'}:TestEngines",
    )
    settings.advertise = "all"
    settings.job_timeout_s = 2
    settings.max_attempts = 2
    with make_client(settings) as c:
        c.headers["Authorization"] = "Bearer tok-alice"
        jid = submit(c, fx("rtf"), "xml", {"sleep_seconds": 60})
        j = wait_state(c, jid, {"FAILED"})
        assert (
            j["error"]["code"] == "timeout"
            and j["error"]["retryable"]
            and j["can_retry"]
            and j["attempts"] == 1
        )
        r = c.post(f"/v1/jobs/{jid}/retry")
        assert r.status_code == 200 and r.json()["state"] == "QUEUED"
        j = wait_state(c, jid, {"FAILED"})
        assert j["attempts"] == 2 and not j["can_retry"]
        assert c.post(f"/v1/jobs/{jid}/retry").status_code == 409  # attempts exhausted
        states = [e["to_state"] for e in c.app.state.rt.store.job_events(jid)]
        assert states.count("RUNNING") == 2 and states[-1] == "FAILED"


def test_engine_crash_is_contained_and_reported(te_client, fx):
    c = te_client
    j = wait_job(c, submit(c, fx("rtf"), "yaml"))
    assert (
        j["state"] == "FAILED"
        and j["error"]["code"] == "conversion_failed"
        and "crashed" in j["error"]["message"]
    )
    assert j["error"]["details"]["signal"] == "SIGSEGV" and not j["error"]["retryable"]
    # the service keeps working after a crashing engine
    assert wait_job(c, submit(c, fx("json"), "yaml"))["state"] == "SUCCEEDED"


def test_memory_limit_kills_runaway_engine(settings, monkeypatch, fx):
    monkeypatch.setenv(
        "EZ_EXTRA_ENGINES",
        f"{__import__('conftest').HELPERS / 'test_engines.py'}:TestEngines",
    )
    settings.advertise = "all"
    settings.worker_memory_mb = 1500
    with make_client(settings) as c:
        c.headers["Authorization"] = "Bearer tok-alice"
        j = wait_job(c, submit(c, fx("rtf"), "csv"))
        assert j["state"] == "FAILED" and j["error"]["code"] == "resource_limit" and j["error"]["retryable"]


def test_invalid_output_is_never_reported_as_success(te_client, fx):
    c = te_client
    j = wait_job(c, submit(c, fx("rtf"), "json"))  # the engine "succeeds" but writes invalid JSON
    assert (
        j["state"] == "FAILED"
        and j["error"]["code"] == "output_validation_failed"
        and j["artifact_id"] is None
    )
    assert any(not ch["passed"] for ch in j["error"]["details"]["failed_checks"])
    assert c.get(f"/v1/jobs/{j['id']}/result").status_code == 409
    assert c.app.state.rt.store.q("SELECT COUNT(*) n FROM artifacts")[0]["n"] == 0
    assert not j["can_retry"]


def _listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    hits = []

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            hits.append(1)
            conn.close()

    threading.Thread(target=loop, daemon=True).start()
    return srv, hits


def test_worker_has_no_network_access_unless_isolation_disabled(settings, monkeypatch, fx):
    from ezconverter.sandbox import network_isolation_available

    if not network_isolation_available():
        pytest.skip("network namespaces unavailable")
    monkeypatch.setenv(
        "EZ_EXTRA_ENGINES",
        f"{__import__('conftest').HELPERS / 'test_engines.py'}:TestEngines",
    )
    settings.advertise = "all"
    srv, hits = _listener()
    port = srv.getsockname()[1]
    try:
        for isolate, expected in ((True, "blocked"), (False, "open")):
            settings.network_isolation = "auto" if isolate else "off"
            with make_client(settings) as c:
                c.headers["Authorization"] = "Bearer tok-alice"
                j = wait_job(c, submit(c, fx("rtf"), "xml", {"probe_port": port}))
                assert j["state"] == "SUCCEEDED"
                body = c.get(c.get(f"/v1/jobs/{j['id']}/result").json()["download_path"]).text
                assert f'net="{expected}"' in body, body
        assert len(hits) == 1  # only the deliberately un-isolated control run reached the listener
    finally:
        srv.close()


def test_worker_environment_is_scrubbed(te_client, fx, monkeypatch):
    monkeypatch.setenv("EZ_API_KEYS", "tok-alice:alice")
    monkeypatch.setenv("DATABASE_PASSWORD", "hunter2")
    j = wait_job(te_client, submit(te_client, fx("rtf"), "svg"))
    text = te_client.get(te_client.get(f"/v1/jobs/{j['id']}/result").json()["download_path"]).text
    names = set(text.split("<text>")[1].split("</text>")[0].split())
    assert names <= {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "EZ_EXTRA_ENGINES",
        "LC_CTYPE",
        "PWD",
        "SHLVL",
        "_",
    }, names
    assert "EZ_API_KEYS" not in names and "DATABASE_PASSWORD" not in names


def test_restart_recovery_requeues_then_completes_and_bounds_attempts(settings, fx, tmp_path):
    rt = build_runtime(settings)  # dispatcher deliberately not started yet
    m, store = rt.manager, rt.store
    from fixtures.generate import fixture

    src = fixture("json")
    import hashlib
    import shutil
    import uuid

    fid = uuid.uuid4().hex
    shutil.copyfile(src, rt.storage.upload_path(fid))
    m.add_file(
        "alice",
        fid,
        "data.json",
        src.stat().st_size,
        hashlib.sha256(src.read_bytes()).hexdigest(),
    )
    lost = m.create_job("alice", fid, "yaml", {}, ExecutionPolicy.LOCAL_ONLY)
    exhausted = m.create_job("alice", fid, "yaml", {}, ExecutionPolicy.LOCAL_ONLY)
    cancelled = m.create_job("alice", fid, "yaml", {}, ExecutionPolicy.LOCAL_ONLY)
    # simulate a crash while three jobs were mid-flight
    store.update_job(lost["id"], state="RUNNING", attempts=1)
    store.update_job(exhausted["id"], state="VERIFYING", attempts=3)
    store.update_job(cancelled["id"], state="RUNNING", attempts=1, cancel_requested=1)
    m.recover()
    assert store.get_job(lost["id"])["state"] == "QUEUED"
    e = store.get_job(exhausted["id"])
    assert e["state"] == "FAILED" and e["error"]["code"] == "worker_lost"
    assert store.get_job(cancelled["id"])["state"] == "CANCELLED"
    m.start()
    try:
        end = time.time() + 60
        while time.time() < end and store.get_job(lost["id"])["state"] != "SUCCEEDED":
            time.sleep(0.1)
        assert store.get_job(lost["id"])["state"] == "SUCCEEDED"
    finally:
        m.stop()
        store.close()


def test_retention_sweep_removes_expired_data_but_keeps_active_work(client, fx):
    rt = client.app.state.rt
    done = wait_job(client, submit(client, fx("json"), "yaml"))
    other = upload(client, fx("csv")).json()  # uploaded, never converted
    root = rt.storage.root
    assert (root / "artifacts" / done["artifact_id"]).exists()
    assert rt.manager.sweep() == {
        "jobs": 0,
        "files": 0,
        "artifacts": 0,
        "batches": 0,
    }  # nothing expired yet
    removed = rt.manager.sweep(now=time.time() + 3 * 3600)
    assert removed["jobs"] == 1 and removed["files"] >= 2
    assert (
        not (root / "artifacts" / done["artifact_id"]).exists()
        and not (root / "uploads" / other["id"]).exists()
    )
    assert client.get(f"/v1/jobs/{done['id']}").status_code == 404
    assert client.get(f"/v1/files/{other['id']}").status_code == 404


def test_storage_quota_and_rate_limit(settings, fx, tmp_path):
    settings.user_quota_mb = 1
    settings.rate_limit_per_min = 6
    big = tmp_path / "big.json"
    big.write_text('["' + "x" * 700_000 + '"]')
    with make_client(settings) as c:
        c.headers["Authorization"] = "Bearer tok-alice"
        assert upload(c, big).status_code == 201
        r = upload(c, big)
        assert r.status_code == 413 and r.json()["error"]["code"] == "quota_exceeded"
        for _ in range(8):
            r = c.post("/v1/files/inspect", params={"filename": "a.json"}, content=b"{}")
            if r.status_code == 429:
                break
        assert (
            r.status_code == 429
            and r.json()["error"]["code"] == "rate_limited"
            and int(r.headers["retry-after"]) >= 1
        )
