import hashlib
import io

from conftest import convert, upload, wait_job
from PIL import Image


def test_health_and_envelope_shapes(client):
    h = client.get("/v1/health").json()
    assert h["status"] == "ok" and h["location"] == "local" and h["api_version"] == "v1"
    assert h["network_isolation"] in ("active", "unavailable", "disabled")
    r = client.get("/v1/nope")
    assert r.status_code == 404 and set(r.json()["error"]) == {
        "code",
        "message",
        "details",
        "request_id",
    }
    assert r.headers["x-request-id"] == r.json()["error"]["request_id"]
    r = client.post("/v1/conversions", json={"file_id": 5})
    assert (
        r.status_code == 422
        and r.json()["error"]["code"] == "invalid_request"
        and r.json()["error"]["details"]["fields"]
    )


def test_capabilities_advertise_only_verified_and_available(client):
    caps = client.get("/v1/capabilities").json()
    assert caps["location"] == "local" and caps["capabilities"] and caps["engines"]
    assert all(c["verified"] and c["available"] for c in caps["capabilities"])
    ids = {c["id"] for c in caps["capabilities"]}
    assert {
        "pillow.png->webp",
        "ffmpeg.mp3->opus",
        "libreoffice.docx->pdf",
        "structured.json->yaml",
    } <= ids
    only = client.get("/v1/capabilities", params={"source": "docx"}).json()["capabilities"]
    assert only and all(c["source"] == "docx" for c in only)
    sample = next(c for c in caps["capabilities"] if c["id"] == "pillow.png->jpeg")
    assert {o["name"] for o in sample["options"]} >= {
        "quality",
        "max_width",
        "background",
    }
    assert sample["license"]["spdx"] and sample["fidelity"] == "lossy"


def test_full_single_conversion_with_download_and_provenance(client, fx):
    src = fx("png")
    f = upload(client, src, "My Photo.png")
    assert f.status_code == 201
    fj = f.json()
    assert (
        fj["format"] == "png"
        and fj["eligible"]
        and fj["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    )
    assert fj["detection"]["details"]["width"] == 96
    targets = {t["target"] for t in client.get(f"/v1/files/{fj['id']}/targets").json()["targets"]}
    assert {"jpeg", "webp", "avif"} <= targets and "svg" not in targets

    plan = client.post(
        "/v1/conversions/plan",
        json={"file_id": fj["id"], "target": "jpeg", "options": {"quality": 70}},
    ).json()
    assert (
        plan["fidelity"] == "lossy"
        and plan["steps"][0]["engine_id"] == "pillow"
        and plan["options"]["quality"] == 70
    )
    assert any("transparent" in w.lower() for w in plan["warnings"])  # the fixture has an alpha channel

    job = client.post(
        "/v1/conversions",
        json={"file_id": fj["id"], "target": "jpeg", "options": {"quality": 70}},
    )
    assert job.status_code == 202 and job.json()["state"] == "QUEUED"
    done = wait_job(client, job.json()["id"])
    assert done["state"] == "SUCCEEDED" and done["progress"] == 1.0
    res = client.get(f"/v1/jobs/{done['id']}/result").json()
    assert res["name"] == "My Photo.jpg" and res["verification"]["ok"] and res["mime"] == "image/jpeg"
    assert all(c["passed"] for c in res["verification"]["checks"])
    prov = res["provenance"]
    assert (
        prov["input_sha256"] == fj["sha256"]
        and prov["output_sha256"] == res["sha256"]
        and prov["tool_versions"]["pillow"]
    )

    d = client.get(res["download_path"])
    assert d.status_code == 200 and hashlib.sha256(d.content).hexdigest() == res["sha256"]
    assert (
        "attachment" in d.headers["content-disposition"] and d.headers["x-content-type-options"] == "nosniff"
    )
    im = Image.open(io.BytesIO(d.content))
    assert im.format == "JPEG" and im.size == (96, 64)

    events = [e["to_state"] for e in client.app.state.rt.store.job_events(done["id"])]
    assert events == [
        "CREATED",
        "VALIDATING",
        "QUEUED",
        "RUNNING",
        "VERIFYING",
        "SUCCEEDED",
    ]


def test_job_without_result_and_unsupported_routes_explain_themselves(client, fx):
    f = upload(client, fx("png")).json()
    r = client.post("/v1/conversions/plan", json={"file_id": f["id"], "target": "svg"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_conversion"
    assert (
        "tracing" in r.json()["error"]["message"]
        and "jpeg" in r.json()["error"]["details"]["available_targets"]
    )
    r = client.post("/v1/conversions/plan", json={"file_id": f["id"], "target": "png"})
    assert (
        r.status_code == 422
        and "already" in r.json()["error"]["message"]
        and "option" in r.json()["error"]["message"]
    )
    ok = client.post(
        "/v1/conversions/plan",
        json={"file_id": f["id"], "target": "png", "options": {"max_width": 48}},
    )
    assert ok.status_code == 200 and ok.json()["fidelity"] == "lossy"
    r = client.post(
        "/v1/conversions/plan",
        json={"file_id": f["id"], "target": "jpeg", "options": {"quality": 1000}},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_options"
    r = client.post(
        "/v1/conversions/plan",
        json={"file_id": f["id"], "target": "jpeg", "options": {"rm_rf": True}},
    )
    assert r.json()["error"]["details"]["unknown"] == ["rm_rf"]
    r = client.post("/v1/conversions/plan", json={"file_id": f["id"], "target": "klingon"})
    assert r.status_code == 422
    j = client.post("/v1/conversions", json={"file_id": f["id"], "target": "webp"}).json()
    # result before completion (or for failed jobs) is a 409 with the state, never a fake result
    pending = client.get(f"/v1/jobs/{j['id']}/result")
    assert pending.status_code in (200, 409)
    wait_job(client, j["id"])


def test_unrecognised_and_damaged_files_cannot_be_converted(client, tmp_path):
    junk = tmp_path / "mystery.dat"
    junk.write_bytes(bytes(range(256)) * 20)
    f = upload(client, junk).json()
    assert not f["eligible"] and "not recognised" in f["ineligible_reason"]
    r = client.post("/v1/conversions", json={"file_id": f["id"], "target": "png"})
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_input"
    empty = client.post("/v1/files", params={"filename": "e.png"}, content=b"")
    assert empty.status_code == 415


def test_corrupt_image_fails_job_without_output(client, tmp_path):
    from fixtures.generate import corrupt

    bad = corrupt("png", tmp_path / "bad.png")
    f = upload(client, bad).json()
    if f["eligible"]:  # detection may already reject; otherwise the job must fail
        j = client.post("/v1/conversions", json={"file_id": f["id"], "target": "jpeg"})
        done = wait_job(client, j.json()["id"])
        assert done["state"] == "FAILED" and done["error"]["code"] in (
            "malformed_input",
            "conversion_failed",
        )
        assert not done["error"]["retryable"] and done["can_retry"] is False
        assert client.get(f"/v1/jobs/{done['id']}/result").status_code == 409
        assert client.post(f"/v1/jobs/{done['id']}/retry").status_code == 409
    else:
        assert f["ineligible_reason"]


def test_inspect_is_stateless(client, fx):
    r = client.post(
        "/v1/files/inspect",
        params={"filename": "x.mp3"},
        content=fx("mp3").read_bytes(),
    )
    assert r.status_code == 200 and r.json()["format"] == "mp3"
    storage = client.app.state.rt.storage.root
    assert not any((storage / "uploads").iterdir())
    assert not any((storage / "tmp").iterdir())


def test_idempotency_key_prevents_duplicate_jobs(client, fx):
    f = upload(client, fx("json")).json()
    body = {"file_id": f["id"], "target": "yaml"}
    a = client.post("/v1/conversions", json=body, headers={"Idempotency-Key": "k-1"})
    b = client.post("/v1/conversions", json=body, headers={"Idempotency-Key": "k-1"})
    assert (
        a.status_code == b.status_code == 202
        and a.json()["id"] == b.json()["id"]
        and b.headers["idempotent-replay"] == "true"
    )
    c = client.post(
        "/v1/conversions",
        json={**body, "target": "csv"},
        headers={"Idempotency-Key": "k-1"},
    )
    assert c.status_code == 409 and c.json()["error"]["code"] == "idempotency_conflict"
    assert len(client.get("/v1/jobs").json()) == 1
    wait_job(client, a.json()["id"])


def test_multi_step_conversion_end_to_end(client, fx):
    done = convert(client, fx("docx"), "png")  # DOCX -> PDF (LibreOffice) -> PNG (Poppler)
    assert done["state"] == "SUCCEEDED" and done["plan"]["multi_step"] and len(done["plan"]["steps"]) == 2
    assert done["plan"]["fidelity"] == "layout_altering" and any("2 steps" in w for w in done["warnings"])
    res = client.get(f"/v1/jobs/{done['id']}/result").json()
    out = client.get(res["download_path"]).content
    assert out[:8] == b"\x89PNG\r\n\x1a\n" and len(res["provenance"]["steps"]) == 2

    done = convert(client, fx("yaml"), "csv")  # YAML -> JSON -> CSV
    assert done["state"] == "SUCCEEDED"
    csv_text = client.get(client.get(f"/v1/jobs/{done['id']}/result").json()["download_path"]).text
    assert csv_text.splitlines()[0] == "id,name,note" and "EZCONVERTER-SENTINEL" in csv_text


def test_options_take_effect(client, fx):
    done = convert(
        client,
        fx("png"),
        "jpeg",
        {"max_width": 48, "quality": 40, "background": "#00ff00"},
    )
    out = client.get(client.get(f"/v1/jobs/{done['id']}/result").json()["download_path"]).content
    im = Image.open(io.BytesIO(out))
    assert im.size == (48, 32)
    done = convert(client, fx("mp4"), "mp4", {"max_height": 120, "mode": "reencode"})
    assert done["state"] == "SUCCEEDED"
    from conftest import wait_job as _w  # noqa: F401

    res = client.get(f"/v1/jobs/{done['id']}/result").json()
    assert any(c["name"] == "height_limit" and c["passed"] for c in res["verification"]["checks"])


def test_cancel_finished_job_is_conflict_and_delete_removes_result(client, fx):
    done = convert(client, fx("json"), "yaml")
    assert client.post(f"/v1/jobs/{done['id']}/cancel").status_code == 409
    art = done["artifact_id"]
    assert client.delete(f"/v1/jobs/{done['id']}").status_code == 204
    assert client.get(f"/v1/jobs/{done['id']}").status_code == 404
    assert client.get(f"/v1/artifacts/{art}/download").status_code == 404
    assert not (client.app.state.rt.storage.root / "artifacts" / art).exists()


def test_signed_download_links(client, fx):
    done = convert(client, fx("json"), "yaml")
    link = client.post(f"/v1/artifacts/{done['artifact_id']}/link").json()
    anon = type(client)(client.app)  # no Authorization header
    ok = anon.get(link["url"])
    assert ok.status_code == 200 and b"EZCONVERTER-SENTINEL" in ok.content
    assert anon.get(link["url"][:-3] + "abc").status_code == 404  # tampered signature
    assert anon.get(link["url"].replace("exp=", "exp=1")).status_code == 404  # tampered expiry
    assert anon.get(f"/v1/artifacts/{done['artifact_id']}/download").status_code == 401
