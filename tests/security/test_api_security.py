import time
from pathlib import Path

import pytest
from conftest import convert, make_client, upload, wait_job

from ezconverter.domain.capability import Location

pytestmark = pytest.mark.security


# ------------------------------------------------------------------ authentication & authorization


def test_every_route_but_health_requires_a_valid_token(client):
    anon = type(client)(client.app)
    for method, path in [
        ("GET", "/v1/capabilities"),
        ("GET", "/v1/jobs"),
        ("POST", "/v1/files?filename=a.png"),
        ("POST", "/v1/conversions"),
        ("POST", "/v1/batches"),
        ("GET", "/v1/targets?source=png"),
        ("GET", "/v1/jobs/" + "0" * 32),
        ("GET", "/v1/batches/" + "0" * 32),
        ("POST", "/v1/files/inspect?filename=a"),
    ]:
        r = anon.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.json()["error"]["code"] == "unauthenticated"
    assert anon.get("/v1/health").status_code == 200
    for bad in (
        "Bearer nope",
        "Bearer ",
        "Basic dG9rLWFsaWNl",
        "tok-alice",
        "Bearer tok-alice-extra",
    ):
        assert type(client)(client.app).get("/v1/jobs", headers={"Authorization": bad}).status_code == 401


def test_users_cannot_touch_each_others_data(client, bob, fx):
    f = upload(client, fx("json")).json()
    done = convert(client, fx("json"), "yaml")
    b = client.post("/v1/batches", json={"items": [{"file_id": f["id"], "target": "yaml"}]}).json()
    art = done["artifact_id"]
    link = client.post(f"/v1/artifacts/{art}/link").json()["url"]
    probes = [
        ("GET", f"/v1/files/{f['id']}"),
        ("DELETE", f"/v1/files/{f['id']}"),
        ("GET", f"/v1/files/{f['id']}/targets"),
        ("GET", f"/v1/jobs/{done['id']}"),
        ("GET", f"/v1/jobs/{done['id']}/result"),
        ("POST", f"/v1/jobs/{done['id']}/cancel"),
        ("POST", f"/v1/jobs/{done['id']}/retry"),
        ("DELETE", f"/v1/jobs/{done['id']}"),
        ("GET", f"/v1/batches/{b['id']}"),
        ("GET", f"/v1/batches/{b['id']}/result"),
        ("GET", f"/v1/batches/{b['id']}/archive"),
        ("POST", f"/v1/batches/{b['id']}/cancel"),
        ("DELETE", f"/v1/batches/{b['id']}"),
        ("GET", f"/v1/artifacts/{art}/download"),
        ("POST", f"/v1/artifacts/{art}/link"),
    ]
    for method, path in probes:
        r = bob.request(method, path)
        assert r.status_code == 404, (
            method,
            path,
            r.status_code,
            r.text,
        )  # 404, not 403: no existence oracle
    assert bob.post("/v1/conversions", json={"file_id": f["id"], "target": "yaml"}).status_code == 404
    assert bob.post("/v1/conversions/plan", json={"file_id": f["id"], "target": "yaml"}).status_code == 404
    assert (
        bob.post("/v1/batches", json={"items": [{"file_id": f["id"], "target": "yaml"}]}).status_code == 404
    )
    assert bob.get("/v1/jobs").json() == []
    # alice's data is untouched and still downloadable
    assert client.get(f"/v1/jobs/{done['id']}").status_code == 200
    assert client.get(f"/v1/artifacts/{art}/download").status_code == 200
    # a signed link is a bearer capability for that artifact only, for a few minutes
    assert type(client)(client.app).get(link).status_code == 200
    other = convert(client, fx("csv"), "json")
    assert type(client)(client.app).get(link.replace(art, other["artifact_id"])).status_code == 404


def test_non_ascii_signature_or_token_is_rejected_cleanly_not_a_500(client, fx):
    art = convert(client, fx("json"), "yaml")["artifact_id"]
    anon = type(client)(client.app, raise_server_exceptions=False)
    exp = int(time.time()) + 300
    # compare_digest raises TypeError on non-ASCII str: that must be a plain 404, not an internal_error
    for sig in ("%C3%A9", "%C3%A9" * 32, "%E2%82%AC", "%F0%9F%94%91"):
        r = anon.get(f"/v1/artifacts/{art}/download?exp={exp}&sig={sig}")
        assert r.status_code == 404, (sig, r.status_code, r.text)
        assert r.json()["error"]["code"] == "not_found"
    # the same goes for a non-ASCII bearer token (header bytes are decoded as latin-1)
    r = anon.get("/v1/jobs", headers={"Authorization": "Bearer t\xe9k".encode("latin-1")})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"
    # valid signatures are unaffected
    link = client.post(f"/v1/artifacts/{art}/link").json()["url"]
    assert anon.get(link).status_code == 200


# ------------------------------------------------------------------ execution policy (hybrid local / remote)


def test_remote_runtime_never_accepts_local_only_or_unspecified_policy(settings, fx, tmp_path):
    settings.location = Location.REMOTE
    with make_client(settings) as c:
        c.headers["Authorization"] = "Bearer tok-alice"
        data = fx("json").read_bytes()
        uploads = c.app.state.rt.storage.root / "uploads"

        r = c.post("/v1/files", params={"filename": "a.json"}, content=data)
        assert r.status_code == 409 and r.json()["error"]["code"] == "remote_consent_required"
        for pol, code in (
            ("LOCAL_ONLY", "local_only_violation"),
            ("AUTO", "remote_consent_required"),
        ):
            r = c.post(
                "/v1/files",
                params={"filename": "a.json"},
                content=data,
                headers={"X-EZ-Execution-Policy": pol},
            )
            assert r.status_code == 409 and r.json()["error"]["code"] == code, (
                pol,
                r.text,
            )
            assert "Nothing was uploaded" in r.json()["error"]["message"] or code == "remote_consent_required"
            r = c.post(
                "/v1/files/inspect",
                params={"filename": "a.json"},
                content=data,
                headers={"X-EZ-Execution-Policy": pol},
            )
            assert r.status_code == 409
        assert not any(uploads.iterdir()), "no byte of a LOCAL_ONLY upload may be stored on a remote runtime"

        ok = c.post(
            "/v1/files",
            params={"filename": "a.json"},
            content=data,
            headers={"X-EZ-Execution-Policy": "REMOTE_ALLOWED"},
        )
        assert ok.status_code == 201
        fid = ok.json()["id"]
        for pol in ("LOCAL_ONLY", "AUTO"):
            assert (
                c.post(
                    "/v1/conversions/plan",
                    json={"file_id": fid, "target": "yaml", "execution_policy": pol},
                ).status_code
                == 409
            )
            assert (
                c.post(
                    "/v1/conversions",
                    json={"file_id": fid, "target": "yaml", "execution_policy": pol},
                ).status_code
                == 409
            )
            assert (
                c.post(
                    "/v1/batches",
                    json={
                        "items": [{"file_id": fid, "target": "yaml"}],
                        "execution_policy": pol,
                    },
                ).status_code
                == 409
            )
        assert c.get("/v1/jobs").json() == []  # nothing was queued behind the user's back
        # a batch item cannot smuggle in a policy the runtime refuses, and nothing is half-created
        b = c.post(
            "/v1/batches",
            json={
                "items": [
                    {"file_id": fid, "target": "yaml"},
                    {
                        "file_id": fid,
                        "target": "yaml",
                        "execution_policy": "LOCAL_ONLY",
                    },
                ],
                "execution_policy": "REMOTE_ALLOWED",
            },
        )
        assert b.status_code == 409 and b.json()["error"]["code"] == "local_only_violation"
        assert (
            c.app.state.rt.store.q("SELECT COUNT(*) n FROM batches")[0]["n"] == 0
            and c.get("/v1/jobs").json() == []
        )


def test_local_runtime_defaults_to_local_only_and_accepts_explicit_policies(client, fx):
    assert upload(client, fx("json")).status_code == 201
    for pol in ("LOCAL_ONLY", "REMOTE_ALLOWED", "AUTO"):
        r = client.post(
            "/v1/files",
            params={"filename": "a.json"},
            content=b"{}",
            headers={"X-EZ-Execution-Policy": pol},
        )
        assert r.status_code == 201
    assert (
        client.post(
            "/v1/files",
            params={"filename": "a.json"},
            content=b"{}",
            headers={"X-EZ-Execution-Policy": "bogus"},
        ).status_code
        == 422
    )


# ------------------------------------------------------------------ upload abuse & hostile names


def test_oversized_upload_is_rejected_and_not_kept(settings, tmp_path):
    settings.max_upload_mb = 1
    with make_client(settings) as c:
        c.headers["Authorization"] = "Bearer tok-alice"
        r = c.post(
            "/v1/files",
            params={"filename": "big.bin"},
            content=b"a" * (2 * 1024 * 1024),
        )
        assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"

        def chunks():  # chunked: no Content-Length to pre-check
            for _ in range(40):
                yield b"b" * 65536

        r = c.post("/v1/files", params={"filename": "big.bin"}, content=chunks())
        assert r.status_code == 413
        assert not any((c.app.state.rt.storage.root / "uploads").iterdir())
        assert c.app.state.rt.store.q("SELECT COUNT(*) n FROM files")[0]["n"] == 0


HOSTILE = [
    "../../../../tmp/pwned.png",
    "..\\..\\windows\\system32\\evil.png",
    "/etc/passwd",
    "a\r\nSet-Cookie: x=1.png",
    "CON.png",
    "nul.png",
    "evil‮gnp.exe",
    "<script>alert(1)</script>.png",
    "x" * 200 + ".png",
    "  ...  .png",
    "a;touch PWNED;.png",
    "$(touch PWNED).png",
    "`touch PWNED`.png",
    "-rf.png",
    "--help.png",
]


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_filenames_are_neutralised(client, fx, tmp_path, name, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rt = client.app.state.rt
    f = upload(client, fx("png"), name)
    assert f.status_code == 201, f.text
    stored = f.json()["original_name"]
    assert (
        "/" not in stored
        and "\\" not in stored
        and "\r" not in stored
        and "\n" not in stored
        and len(stored) <= 140
    )
    done = wait_job(
        client,
        client.post("/v1/conversions", json={"file_id": f.json()["id"], "target": "webp"}).json()["id"],
    )
    assert done["state"] == "SUCCEEDED"
    res = client.get(f"/v1/jobs/{done['id']}/result").json()
    d = client.get(res["download_path"])
    cd = d.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd and cd.startswith("attachment;")
    assert d.content[:4] == b"RIFF"
    for danger in (tmp_path / "PWNED", Path("/tmp/pwned.png"), Path("/tmp/pwned.webp")):
        assert not danger.exists()
    # every stored byte lives under the data dir with server-generated names
    for p in rt.storage.root.rglob("*"):
        if p.is_file():
            assert p.name in (
                "blob",
                "ezconverter.db",
                "ezconverter.db-wal",
                "ezconverter.db-shm",
            ) or p.suffix in ("", ".db"), p


def test_overlong_or_empty_filename_query_is_rejected(client):
    assert client.post("/v1/files", params={"filename": "x" * 300}, content=b"abc").status_code == 422
    assert client.post("/v1/files", params={"filename": ""}, content=b"abc").status_code == 422


def test_spoofed_content_type_and_extension_are_ignored(client, tmp_path, fx):
    r = client.post(
        "/v1/files",
        params={"filename": "photo.png"},
        content=b"MZ\x90\x00 not an image at all, just text\n" * 5,
        headers={"Content-Type": "image/png"},
    )
    assert r.status_code == 201 and r.json()["format"] != "png"
    f = r.json()
    p = client.post("/v1/conversions/plan", json={"file_id": f["id"], "target": "jpeg"})
    assert p.status_code in (415, 422)
    exe_named_png = tmp_path / "x.exe"
    exe_named_png.write_bytes(fx("png").read_bytes())
    g = upload(client, exe_named_png, "totally-safe.exe").json()
    assert g["format"] == "png" and g["detection"]["extension_format"] is None


def test_option_values_cannot_inject_commands_or_arguments(client, fx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = upload(client, fx("mp4")).json()
    evil = [
        "; touch PWNED",
        "$(touch PWNED)",
        "`touch PWNED`",
        "-f lavfi -i x",
        "../../x",
        "1 -y /tmp/PWNED2",
    ]
    for bad in evil:
        for opt in ("max_height", "fps", "crf", "mode", "subtitles"):
            r = client.post(
                "/v1/conversions/plan",
                json={"file_id": f["id"], "target": "webm", "options": {opt: bad}},
            )
            assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_options", (opt, bad, r.text)
    p = upload(client, fx("png")).json()
    r = client.post(
        "/v1/conversions/plan",
        json={
            "file_id": p["id"],
            "target": "jpeg",
            "options": {"background": "`touch PWNED`"},
        },
    )
    assert r.status_code == 422
    assert not (tmp_path / "PWNED").exists() and not Path("/tmp/PWNED2").exists()


# ------------------------------------------------------------------ hostile document / data inputs


def test_pandoc_does_not_embed_local_files_referenced_by_markdown(client, tmp_path, fx):
    secret = tmp_path / "secret.png"
    secret.write_bytes(fx("png").read_bytes())
    md = tmp_path / "evil.md"
    md.write_text(
        f'# t\n\n![x]({secret})\n\n![y](file://{secret})\n\n![z](/etc/hostname)\n\n<img src="{secret}">\n\n```{{=openxml}}\n<w:p/>\n```\n'
    )
    for target in ("docx", "odt", "html"):
        done = convert(client, md, target)
        assert done["state"] == "SUCCEEDED", done["error"]
        out = client.get(client.get(f"/v1/jobs/{done['id']}/result").json()["download_path"]).content
        if target == "html":
            assert str(secret).encode() not in out and b"<img" not in out and b"base64" not in out
        else:
            import io
            import zipfile

            names = zipfile.ZipFile(io.BytesIO(out)).namelist()
            assert not any(
                "media" in n.lower() or n.lower().endswith(".png") or "Pictures" in n for n in names
            ), names


def test_libreoffice_does_not_fetch_remote_resources_or_run_macros(client, tmp_path):
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    hits = []

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            hits.append(c.recv(200))
            c.close()

    threading.Thread(target=loop, daemon=True).start()
    html = tmp_path / "beacon.html"
    html.write_text(
        f'<html><body><p>hello</p><img src="http://127.0.0.1:{port}/track.png"><link rel="stylesheet" href="http://127.0.0.1:{port}/a.css">'
        f'<img src="https://127.0.0.1:{port}/secure.png"><img src="ftp://127.0.0.1:{port}/f.png"></body></html>'
    )
    try:
        done = convert(client, html, "pdf")
        assert done["state"] == "SUCCEEDED"
        time.sleep(0.5)
        assert hits == [], "LibreOffice reached out to the network from inside the worker"
    finally:
        srv.close()


def test_svg_with_doctype_is_still_recognised_as_svg(client, tmp_path):
    p = tmp_path / "illustrator.svg"
    p.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!-- Generator: Illustrator -->\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" [<!ENTITY ns "http://www.w3.org/2000/svg">]>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
    )
    assert upload(client, p).json()["format"] == "svg"


def test_xml_entity_bomb_and_xxe_are_rejected(client, tmp_path):
    bomb = tmp_path / "bomb.xml"
    bomb.write_text(
        '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
        '<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]><l>&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;</l>'
    )
    xxe = tmp_path / "xxe.xml"
    xxe.write_text('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]><r>&x;</r>')
    for p in (bomb, xxe):
        t = time.time()
        done = convert(client, p, "json")
        assert (
            done["state"] == "FAILED" and done["error"]["code"] == "malformed_input" and time.time() - t < 20
        ), done
        assert "hostname" not in str(done)


def test_yaml_alias_bomb_and_deep_nesting_fail_safely(client, tmp_path):
    lines = ['a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]']
    prev = "a"
    for i in range(9):
        cur = chr(ord("b") + i)
        lines.append(f"{cur}: &{cur} [" + ",".join([f"*{prev}"] * 9) + "]")
        prev = cur
    bomb = tmp_path / "bomb.yaml"
    bomb.write_text("\n".join(lines) + "\n")
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 20000 + "]" * 20000)
    t = time.time()
    done = convert(client, bomb, "json")  # accepted as YAML, then stopped by the expansion budget
    assert done["state"] == "FAILED" and done["error"]["code"] == "unsafe_input" and time.time() - t < 30, (
        done
    )
    f = upload(client, deep).json()  # 20 000 levels: cannot be parsed, so it is never treated as JSON
    assert f["format"] == "txt"
    r = client.post("/v1/conversions", json={"file_id": f["id"], "target": "yaml"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_conversion"


def test_csv_formula_injection_can_be_neutralised(client, tmp_path):
    p = tmp_path / "x.json"
    p.write_text('[{"a": "=HYPERLINK(\\"http://evil\\")", "b": "+1+1", "c": "safe", "d": "@SUM(1)"}]')
    plain = convert(client, p, "csv")
    safe = convert(client, p, "csv", {"neutralize_formulas": True})
    get = lambda j: client.get(client.get(f"/v1/jobs/{j['id']}/result").json()["download_path"]).text
    assert "=HYPERLINK" in get(plain).splitlines()[1]
    row = get(safe).splitlines()[1]
    assert "'=HYPERLINK" in row and "'+1+1" in row and "'@SUM" in row and ",safe," in row


def test_image_bomb_is_rejected_before_decoding(client, tmp_path):
    from PIL import Image

    bomb = tmp_path / "bomb.png"
    Image.new("1", (40000, 40000)).save(bomb)
    f = upload(client, bomb).json()
    assert not f["eligible"] and "safely" in f["ineligible_reason"]
    r = client.post("/v1/conversions", json={"file_id": f["id"], "target": "jpeg"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsafe_input"
