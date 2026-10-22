import io
import json
import time
import zipfile

from conftest import upload, wait_batch


def _batch(c, items, policy="LOCAL_ONLY", key=None):
    h = {"Idempotency-Key": key} if key else {}
    return c.post("/v1/batches", json={"items": items, "execution_policy": policy}, headers=h)


def test_mixed_batch_reports_independent_outcomes_and_packages_only_successes(client, fx, tmp_path):
    from fixtures.generate import corrupt

    bad = corrupt("png", tmp_path / "broken.png")
    files = {
        "ok_png": upload(client, fx("png"), "report.png").json(),
        "ok_json": upload(client, fx("json"), "report.json").json(),
        "ok_csv": upload(client, fx("csv"), "REPORT.csv").json(),
        "bad_png": upload(client, bad, "broken.png").json(),
        "unsupported": upload(client, fx("mp3"), "song.mp3").json(),
    }
    items = [
        {"file_id": files["ok_png"]["id"], "target": "webp"},
        {"file_id": files["ok_json"]["id"], "target": "yaml"},
        {
            "file_id": files["ok_csv"]["id"],
            "target": "yaml",
        },  # two files will produce report.yaml / REPORT.yaml
        {"file_id": files["bad_png"]["id"], "target": "webp"},
        {"file_id": files["unsupported"]["id"], "target": "docx"},  # invalid route
    ]
    r = _batch(client, items)
    assert r.status_code == 202, r.text
    b = wait_batch(client, r.json()["id"])
    states = [j["state"] for j in b["jobs"]]
    assert states.count("SUCCEEDED") == 3 and states.count("FAILED") == 2, b["counts"]
    assert b["progress"] == 1.0 and b["archive_available"]
    failed = {j["original_name"]: j for j in b["jobs"] if j["state"] == "FAILED"}
    assert (
        failed["song.mp3"]["error"]["code"] == "unsupported_conversion"
        and not failed["song.mp3"]["can_retry"]
    )
    assert failed["broken.png"]["error"]["code"] in (
        "malformed_input",
        "unsupported_input",
        "conversion_failed",
    )
    assert all(j["artifact_id"] is None for j in failed.values())

    res = client.get(f"/v1/batches/{b['id']}/result").json()
    assert len(res["succeeded"]) == 3 and len(res["failed"]) == 2 and res["cancelled"] == []

    z = client.get(res["archive_path"])
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    names = zf.namelist()
    assert zf.testzip() is None
    outputs = [n for n in names if n != "manifest.json"]
    assert len(outputs) == 3 and len({n.lower() for n in outputs}) == 3, outputs  # collision resolved
    assert "report.webp" in outputs and any(n.endswith(" (1).yaml") for n in outputs)
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["summary"] == {
        "total": 5,
        "succeeded": 3,
        "failed": 2,
        "cancelled": 0,
    }
    by = {e["original_name"]: e for e in manifest["entries"]}
    assert (
        by["song.mp3"]["status"] == "FAILED"
        and by["song.mp3"]["output_name"] is None
        and by["song.mp3"]["error"]["code"]
    )
    assert by["report.png"]["output_name"] == "report.webp" and by["report.png"]["sha256"]
    assert not any("broken" in n or "song" in n for n in outputs)  # failed inputs never appear as outputs
    assert zf.read("report.webp")[:4] == b"RIFF"


def test_batch_idempotency_and_limits(client, fx):
    f = upload(client, fx("json")).json()
    items = [{"file_id": f["id"], "target": "yaml"}]
    a = _batch(client, items, key="b1")
    b = _batch(client, items, key="b1")
    assert a.json()["id"] == b.json()["id"] and b.headers["idempotent-replay"] == "true"
    wait_batch(client, a.json()["id"])
    assert _batch(client, []).status_code == 422
    client.app.state.rt.settings.max_batch_files = 2
    assert _batch(client, items * 3).status_code == 422
    assert _batch(client, [{"file_id": "0" * 32, "target": "yaml"}]).status_code == 404


def test_archive_unavailable_while_running_or_when_nothing_succeeded(client, fx, tmp_path, monkeypatch):
    f = upload(client, fx("mp3"), "s.mp3").json()
    r = _batch(client, [{"file_id": f["id"], "target": "docx"}])
    b = wait_batch(client, r.json()["id"])
    assert not b["archive_available"]
    resp = client.get(f"/v1/batches/{b['id']}/archive")
    assert resp.status_code == 409 and "nothing to download" in resp.json()["error"]["message"]


def test_retry_failed_and_delete_batch(client, fx):
    f = upload(client, fx("json")).json()
    b = wait_batch(client, _batch(client, [{"file_id": f["id"], "target": "yaml"}]).json()["id"])
    again = client.post(f"/v1/batches/{b['id']}/retry-failed").json()  # nothing retryable: no change
    assert again["counts"] == {"SUCCEEDED": 1}
    art = b["jobs"][0]["artifact_id"]
    assert client.delete(f"/v1/batches/{b['id']}").status_code == 204
    assert client.get(f"/v1/batches/{b['id']}").status_code == 404
    assert client.get(f"/v1/artifacts/{art}/download").status_code == 404


def test_bounded_concurrency_never_exceeds_limit(client, fx, monkeypatch):
    rt = client.app.state.rt
    assert rt.settings.max_concurrency == 2
    f = upload(client, fx("mp4"), "clip.mp4").json()
    items = [{"file_id": f["id"], "target": t} for t in ("webm", "mkv", "mov", "avi")]
    peak = 0
    r = _batch(client, items)
    bid = r.json()["id"]
    end = time.time() + 120
    while time.time() < end:
        peak = max(peak, len(rt.manager._running))
        b = client.get(f"/v1/batches/{bid}").json()
        if b["state"] == "COMPLETED":
            break
        time.sleep(0.02)
    assert b["counts"] == {"SUCCEEDED": 4} and 1 <= peak <= 2
