#!/usr/bin/env python
"""Smoke-test a packaged converter sidecar the way the desktop app drives it.

    python scripts/smoke_packaged.py apps/desktop/sidecar/ezconverter-server.exe [--engines apps/desktop/engines]

Starts the executable (token auth, loopback, random port), then converts real files through the HTTP API:
every conversion runs in a sandboxed `worker` child process of the same executable. Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

TOKEN = "smoke-token"


def call(base: str, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(base + path, data=body, method=method, headers={"Authorization": f"Bearer {TOKEN}", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:  # nosec B310 - loopback test server
        return r.status, r.read()


def check(ok: bool, message: str) -> None:
    if not ok:
        raise SystemExit(f"SMOKE FAILED: {message}")


def convert(base: str, name: str, data: bytes, target: str) -> bytes:
    _, raw = call(base, "POST", f"/v1/files?filename={name}", data)
    fid = json.loads(raw)["id"]
    body = json.dumps({"file_id": fid, "target": target, "options": {}}).encode()
    _, raw = call(base, "POST", "/v1/conversions", body, {"Content-Type": "application/json"})
    jid = json.loads(raw)["id"]
    end = time.time() + 180
    while time.time() < end:
        job = json.loads(call(base, "GET", f"/v1/jobs/{jid}")[1])
        if job["state"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.2)
    if job["state"] != "SUCCEEDED":
        raise SystemExit(f"{name} -> {target}: {job['state']} {job.get('error')}")
    res = json.loads(call(base, "GET", f"/v1/jobs/{jid}/result")[1])
    check(res["verification"]["ok"], f"verification failed: {res}")
    return call(base, "GET", res["download_path"])[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", type=Path)
    ap.add_argument("--engines", type=Path)
    a = ap.parse_args()
    data_dir = tempfile.mkdtemp(prefix="ez-smoke-")
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": os.environ.get("SYSTEMROOT", "") and os.path.join(os.environ["SYSTEMROOT"], "System32"),
           "EZ_DATA_DIR": data_dir, "EZ_AUTH_MODE": "token", "EZ_API_KEYS": f"{TOKEN}:smoke", "EZ_LOCATION": "local"}
    if a.engines:
        env["EZ_ENGINE_DIR"] = str(a.engines.resolve())
        env["PATH"] = f"{env['EZ_ENGINE_DIR']}{os.pathsep}{env['PATH']}"
    proc = subprocess.Popen([str(a.exe), "serve", "--sidecar", "--port", "0"], env=env, cwd=data_dir, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        line = ""
        for line in proc.stdout:  # type: ignore[union-attr]
            if line.startswith("EZCONVERTER_READY"):
                break
        info = json.loads(line.split(" ", 1)[1])
        base = f"http://{info['host']}:{info['port']}"
        health = json.loads(call(base, "GET", "/v1/health")[1])
        caps = json.loads(call(base, "GET", "/v1/capabilities")[1])["capabilities"]
        print(f"health={health['status']} network_isolation={health['network_isolation']} capabilities={len(caps)}")
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "PNG")
        out = convert(base, "a.png", buf.getvalue(), "webp")
        check(out[:4] == b"RIFF", "png -> webp did not produce a WebP")
        print("png -> webp ok (Pillow worker)")
        out = convert(base, "a.json", b'{"a": [1, 2, 3]}', "yaml")
        check(b"a:" in out, "json -> yaml output is wrong")
        print("json -> yaml ok (structured worker)")
        ids = {c["id"] for c in caps}
        if "ffmpeg.wav->mp3" in ids:
            w = io.BytesIO()
            with wave.open(w, "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(22050)
                f.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(22050)))
            out = convert(base, "a.wav", w.getvalue(), "mp3")
            check(out[:3] == b"ID3" or out[:2] in (b"\xff\xfb", b"\xff\xf3"), f"wav -> mp3 is not MP3 data: {out[:4]!r}")
            print("wav -> mp3 ok (bundled ffmpeg)")
        else:
            print("ffmpeg capabilities not advertised (engine not found)")
        if any(c["id"].startswith("pandoc.") for c in caps):
            out = convert(base, "a.md", b"# Hi\n\nsome *text*\n", "html")
            check(b"<h1" in out, "md -> html output is wrong")
            print("md -> html ok (bundled pandoc)")
        print("SMOKE OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
