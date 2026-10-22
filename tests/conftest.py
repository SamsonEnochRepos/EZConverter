from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

from fixtures.generate import fixture  # noqa: E402

from ezconverter.api.app import create_app  # noqa: E402
from ezconverter.config import Settings  # noqa: E402

HELPERS = Path(__file__).parent / "helpers"
FINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


@pytest.fixture(scope="session")
def fx():
    """fx('png') -> Path to a real sample file."""
    return fixture


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        auth_mode="token",
        api_keys={"tok-alice": "alice", "tok-bob": "bob"},
        max_concurrency=2,
        sweep_interval_s=3600,
        retention_hours=1,
        rate_limit_per_min=100000,
        job_timeout_s=240,
        max_upload_mb=64,
    )


def make_client(settings: Settings, monkeypatch=None, **kw):
    for k, v in kw.items():
        setattr(settings, k, v)
    app = create_app(settings)
    return TestClient(app)


@pytest.fixture()
def client(settings):
    with make_client(settings) as c:
        c.headers["Authorization"] = "Bearer tok-alice"
        yield c


@pytest.fixture()
def bob(client):
    c = TestClient(client.app)
    c.headers["Authorization"] = "Bearer tok-bob"
    return c


def upload(c: TestClient, path: Path, name: str | None = None, **headers):
    return c.post(
        "/v1/files",
        params={"filename": name or path.name},
        content=path.read_bytes(),
        headers=headers,
    )


def wait_job(c: TestClient, job_id: str, timeout: float = 240.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/v1/jobs/{job_id}").json()
        if j["state"] in FINAL:
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish: {j}")


def wait_batch(c: TestClient, batch_id: str, timeout: float = 240.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        b = c.get(f"/v1/batches/{batch_id}").json()
        if b["state"] == "COMPLETED":
            return b
        time.sleep(0.15)
    raise AssertionError(f"batch {batch_id} did not finish: {b}")


def convert(
    c: TestClient,
    path: Path,
    target: str,
    options: dict | None = None,
    name: str | None = None,
) -> dict:
    f = upload(c, path, name)
    assert f.status_code == 201, f.text
    r = c.post(
        "/v1/conversions",
        json={"file_id": f.json()["id"], "target": target, "options": options or {}},
    )
    assert r.status_code == 202, r.text
    return wait_job(c, r.json()["id"])


@pytest.fixture()
def te_client(settings, monkeypatch):
    """Client whose runtime also loads the TEST-ONLY fault-injection engines (rtf -> xml/yaml/csv/json/svg)."""
    monkeypatch.setenv("EZ_EXTRA_ENGINES", f"{HELPERS / 'test_engines.py'}:TestEngines")
    settings.advertise = "all"
    with make_client(settings) as c:
        c.headers["Authorization"] = "Bearer tok-alice"
        yield c


def wait_state(c: TestClient, job_id: str, states: set[str], timeout: float = 60.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/v1/jobs/{job_id}").json()
        if j["state"] in states:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {states}: {j}")


def submit(
    c: TestClient,
    path: Path,
    target: str,
    options: dict | None = None,
    name: str | None = None,
) -> str:
    f = upload(c, path, name)
    assert f.status_code == 201, f.text
    r = c.post(
        "/v1/conversions",
        json={"file_id": f.json()["id"], "target": target, "options": options or {}},
    )
    assert r.status_code == 202, r.text
    return r.json()["id"]
