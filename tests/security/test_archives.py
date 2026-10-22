import gzip
import io
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest
from conftest import convert

from ezconverter.domain.errors import ErrorCode, EZError
from ezconverter.engines import archives as A
from ezconverter.engines.base import ExecutionContext, InputInfo
from ezconverter.registry import default_registry
from ezconverter.sandbox import Limits

pytestmark = pytest.mark.security
REG = default_registry()


def run(src: Path, s: str, t: str, tmp_path: Path):
    entry = REG.get(f"archive.{s}->{t}")
    out, work = tmp_path / "out", tmp_path / "work"
    out.mkdir()
    work.mkdir()
    ctx = ExecutionContext(
        entry.capability,
        InputInfo(src, src.name, s, src.stat().st_size, {}),
        {},
        out,
        work,
        "r",
        Limits(isolate_network=False),
        threading.Event(),
    )
    return entry.adapter.execute(ctx), out


def mkzip(path: Path, members: dict, **kw) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def mktar(path: Path, entries) -> Path:
    with tarfile.open(path, "w") as t:
        for ti, data in entries:
            t.addfile(ti, io.BytesIO(data) if data is not None else None)
    return path


def reg(name, data=b"x"):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    return ti, data


@pytest.mark.parametrize(
    "name",
    [
        "../evil.txt",
        "a/../../evil.txt",
        "/etc/cron.d/evil",
        "..\\..\\evil.txt",
        "C:/Windows/evil.txt",
        "ok/../../../evil",
        "x/" + "y" * 300,
    ],
)  # NUL is truncated by zipfile itself
def test_zip_with_unsafe_member_names_is_refused(tmp_path, name):
    src = mkzip(tmp_path / "evil.zip", {"fine.txt": b"ok", name: b"pwn"})
    with pytest.raises(EZError) as e:
        run(src, "zip", "tar", tmp_path)
    assert e.value.code is ErrorCode.UNSAFE_INPUT
    assert not (tmp_path / "evil.txt").exists() and not Path("/etc/cron.d/evil").exists()


@pytest.mark.parametrize("name", ["../evil.txt", "/abs/evil", "a/../../evil"])
def test_tar_with_unsafe_member_names_is_refused(tmp_path, name):
    src = mktar(tmp_path / "evil.tar", [reg("fine.txt"), reg(name)])
    with pytest.raises(EZError) as e:
        run(src, "tar", "zip", tmp_path)
    assert e.value.code is ErrorCode.UNSAFE_INPUT


def test_symlinks_and_hardlinks_are_never_followed_or_copied(tmp_path):
    sym = tarfile.TarInfo("link-to-passwd")
    sym.type = tarfile.SYMTYPE
    sym.linkname = "/etc/passwd"
    hard = tarfile.TarInfo("hard")
    hard.type = tarfile.LNKTYPE
    hard.linkname = "fine.txt"
    dev = tarfile.TarInfo("dev")
    dev.type = tarfile.CHRTYPE
    fifo = tarfile.TarInfo("pipe")
    fifo.type = tarfile.FIFOTYPE
    src = mktar(
        tmp_path / "links.tar",
        [
            reg("fine.txt", b"safe"),
            (sym, None),
            (hard, None),
            (dev, None),
            (fifo, None),
        ],
    )
    res, out = run(src, "tar", "zip", tmp_path)
    with zipfile.ZipFile(res.output_path) as z:
        assert z.namelist() == ["fine.txt"]
        assert b"root:" not in b"".join(z.read(n) for n in z.namelist())
    assert any("skipped" in w for w in res.warnings)

    zsrc = tmp_path / "sym.zip"
    with zipfile.ZipFile(zsrc, "w") as z:
        zi = zipfile.ZipInfo("lnk")
        zi.external_attr = (0o120777) << 16
        z.writestr(zi, "/etc/passwd")
        z.writestr("fine.txt", "safe")
    work = tmp_path / "z"
    work.mkdir()
    res, _ = run(zsrc, "zip", "tgz", work)
    with tarfile.open(res.output_path) as t:
        assert t.getnames() == ["fine.txt"] and all(m.isreg() for m in t.getmembers())


def test_duplicate_members_first_one_wins_deterministically(tmp_path):
    src = tmp_path / "dup.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.txt", "first")
        z.writestr("a.txt", "second")
        z.writestr("b.txt", "b")
    res, _ = run(src, "zip", "tar", tmp_path)
    with tarfile.open(res.output_path) as t:
        assert t.extractfile("a.txt").read() == b"first" and sorted(t.getnames()) == [
            "a.txt",
            "b.txt",
        ]


def test_member_count_and_expansion_ratio_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "MAX_MEMBERS", 50)
    src = mkzip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(200)})
    with pytest.raises(EZError) as e:
        run(src, "zip", "tar", tmp_path)
    assert e.value.code is ErrorCode.UNSAFE_INPUT and "members" in e.value.message

    monkeypatch.setattr(A, "MAX_MEMBERS", 10_000)
    monkeypatch.setattr(A, "RATIO_MIN_BYTES", 1024 * 1024)
    monkeypatch.setattr(A, "MAX_RATIO", 100)
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\0" * (50 * 1024 * 1024))
    work = tmp_path / "w2"
    work.mkdir()
    with pytest.raises(EZError) as e:
        run(bomb, "zip", "tgz", work)
    assert e.value.code is ErrorCode.UNSAFE_INPUT and "bomb" in e.value.message
    assert not list(work.rglob("*.tar.gz"))  # nothing was expanded to disk


def test_total_size_cap_and_gzip_bomb(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "MAX_TOTAL_BYTES", 10 * 1024 * 1024)
    big = tmp_path / "big.zip"
    with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.bin", b"\0" * (20 * 1024 * 1024))
    with pytest.raises(EZError) as e:
        run(big, "zip", "tar", tmp_path)
    assert e.value.code is ErrorCode.UNSAFE_INPUT
    gz = tmp_path / "bomb.gz"
    gz.write_bytes(gzip.compress(b"\0" * (30 * 1024 * 1024)))
    entry = REG.get("gzip.gz->txt")
    out, work = tmp_path / "go", tmp_path / "gw"
    out.mkdir()
    work.mkdir()
    ctx = ExecutionContext(
        entry.capability,
        InputInfo(gz, "bomb.gz", "gz", gz.stat().st_size, {}),
        {},
        out,
        work,
        "r",
        Limits(isolate_network=False),
        threading.Event(),
    )
    with pytest.raises(EZError) as e:
        entry.adapter.execute(ctx)
    assert e.value.code is ErrorCode.UNSAFE_INPUT


def test_encrypted_zip_is_refused(tmp_path):
    src = tmp_path / "enc.zip"
    mkzip(src, {"a.txt": b"secret"})
    raw = bytearray(src.read_bytes())
    # set the "encrypted" general-purpose flag bit in both the local and central headers
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        off = 0
        while (off := raw.find(sig, off)) != -1:
            pos = off + (6 if sig == b"PK\x03\x04" else 8)
            raw[pos] |= 0x1
            off += 4
    src.write_bytes(bytes(raw))
    with pytest.raises(EZError) as e:
        run(src, "zip", "tar", tmp_path)
    assert e.value.code in (ErrorCode.UNSUPPORTED_INPUT, ErrorCode.MALFORMED_INPUT)


def test_nested_archives_are_opaque_files_not_recursed(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("../../escape.txt", "x")  # hostile content inside a nested archive is never opened
    src = mkzip(tmp_path / "outer.zip", {"inner.zip": inner.getvalue(), "readme.txt": b"hi"})
    res, _ = run(src, "zip", "tar", tmp_path)
    with tarfile.open(res.output_path) as t:
        assert sorted(t.getnames()) == ["inner.zip", "readme.txt"]
        assert t.extractfile("inner.zip").read() == inner.getvalue()


def test_truncated_and_corrupt_archives_fail_cleanly(tmp_path):
    good = mkzip(tmp_path / "g.zip", {"a.txt": b"a" * 5000})
    p = tmp_path / "trunc.zip"
    p.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 2])
    d1 = tmp_path / "d1"
    d1.mkdir()
    with pytest.raises(EZError) as e:
        run(p, "zip", "tar", d1)
    assert e.value.code in (ErrorCode.MALFORMED_INPUT, ErrorCode.CONVERSION_FAILED)
    t = tmp_path / "t.tar"
    mktar(t, [reg("a", b"a" * 5000)])
    p = tmp_path / "trunc.tar"
    p.write_bytes(t.read_bytes()[:700])
    d = tmp_path / "dd"
    d.mkdir()
    with pytest.raises(EZError):
        run(p, "tar", "zip", d)


def test_malicious_archive_end_to_end_fails_without_output(client, tmp_path):
    src = mkzip(
        tmp_path / "attack.zip",
        {"fine.txt": b"ok", "../../evil.sh": b"#!/bin/sh\nrm -rf /"},
    )
    done = convert(client, src, "tgz")
    assert (
        done["state"] == "FAILED"
        and done["error"]["code"] == "unsafe_input"
        and not done["error"]["retryable"]
    )
    assert done["artifact_id"] is None
    assert not list(tmp_path.rglob("evil.sh")) and not Path("/evil.sh").exists()
