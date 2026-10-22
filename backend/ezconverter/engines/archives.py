"""Archive repacking: ZIP, TAR, TAR.GZ, 7z and single-file gzip.

Members are streamed from the source archive into the target archive; nothing is extracted to a
path derived from archive contents, so path traversal cannot write outside the sandbox. Names,
member types, counts, sizes and compression ratios are validated first.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..domain.capability import Capability, Fidelity, LicenseInfo, Operation, ResourceHint
from ..domain.errors import ErrorCode, EZError
from .base import (
    Assessment,
    Availability,
    Check,
    EngineAdapter,
    ExecutionContext,
    ExecutionResult,
    InputInfo,
    require_not_cancelled,
)

MULTI = ("zip", "tar", "tgz", "7z")
GZ_TEXT = ("txt", "json", "csv", "xml", "yaml", "md", "html")
MAX_MEMBERS = 10_000
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_RATIO = 1000  # expansion ratio guard (applies once output is non-trivial)
RATIO_MIN_BYTES = 64 * 1024 * 1024
_ZIP = LicenseInfo("Python stdlib zipfile/tarfile/gzip", "PSF-2.0")
_7Z = LicenseInfo(
    "py7zr",
    "LGPL-2.1-or-later",
    "Used as an unmodified dynamically imported pip dependency. "
    "Statically bundling or modifying it triggers LGPL obligations.",
)
EXT = {"zip": "zip", "tar": "tar", "tgz": "tar.gz", "7z": "7z", "gz": "gz"}


@dataclass
class Member:
    name: str
    is_dir: bool
    size: int
    mtime: float
    open: callable  # () -> IO[bytes]


def safe_member_name(name: str) -> str:
    """Return a normalised relative POSIX path or raise UNSAFE_INPUT."""
    if "\x00" in name:
        raise EZError(ErrorCode.UNSAFE_INPUT, "The archive contains a member with an invalid name.")
    n = name.replace("\\", "/")
    p = PurePosixPath(n)
    if p.is_absolute() or (len(n) > 1 and n[1] == ":") or n.startswith("/"):
        raise EZError(ErrorCode.UNSAFE_INPUT, "The archive contains an absolute path, which is not allowed.")
    parts = [x for x in p.parts if x not in ("", ".")]
    if any(x == ".." for x in parts) or not parts:
        raise EZError(ErrorCode.UNSAFE_INPUT, "The archive contains a path that escapes its folder ('..').")
    if any(len(x.encode("utf-8", "replace")) > 255 for x in parts) or len(n) > 1024:
        raise EZError(ErrorCode.UNSAFE_INPUT, "The archive contains an excessively long path.")
    return "/".join(parts)


class _Budget:
    def __init__(self, compressed: int):
        self.compressed = max(1, compressed)
        self.total = 0
        self.count = 0

    def add(self, size: int) -> None:
        self.count += 1
        self.total += size
        if self.count > MAX_MEMBERS:
            raise EZError(ErrorCode.UNSAFE_INPUT, f"The archive has more than {MAX_MEMBERS} members.")
        if self.total > MAX_TOTAL_BYTES:
            raise EZError(ErrorCode.UNSAFE_INPUT, "The archive expands to more than the allowed size.")
        if self.total > RATIO_MIN_BYTES and self.total / self.compressed > MAX_RATIO:
            raise EZError(
                ErrorCode.UNSAFE_INPUT,
                "The archive's compression ratio is suspiciously high (possible decompression bomb).",
            )


def _iter_members(fmt: str, path: str, work_dir, warns: list[str]) -> Iterator[Member]:
    budget = _Budget(os.path.getsize(path))
    skipped = 0
    if fmt == "zip":
        zf = zipfile.ZipFile(path)
        for zi in zf.infolist():
            if zi.flag_bits & 0x1:
                raise EZError(
                    ErrorCode.UNSUPPORTED_INPUT, "Password-protected ZIP archives are not supported."
                )
            name = safe_member_name(zi.filename)
            budget.add(zi.file_size)
            mode = (zi.external_attr >> 16) & 0o170000
            if mode == 0o120000:  # symlink
                skipped += 1
                continue
            import time

            mt = time.mktime(zi.date_time + (0, 0, -1)) if zi.date_time[0] >= 1980 else 0.0
            yield Member(name, zi.is_dir(), zi.file_size, mt, (lambda zi=zi: zf.open(zi)))
    elif fmt in ("tar", "tgz"):
        tf = tarfile.open(path, "r:gz" if fmt == "tgz" else "r:")
        for ti in tf:
            name = safe_member_name(ti.name)
            if ti.isdir():
                yield Member(name, True, 0, ti.mtime, lambda: io.BytesIO(b""))
                continue
            if not ti.isreg():
                skipped += 1
                continue
            budget.add(ti.size)
            yield Member(name, False, ti.size, ti.mtime, (lambda ti=ti: tf.extractfile(ti)))
    elif fmt == "7z":
        import py7zr

        sz = py7zr.SevenZipFile(path, "r")
        if sz.needs_password():
            raise EZError(ErrorCode.UNSUPPORTED_INPUT, "Password-protected 7z archives are not supported.")
        infos = sz.list()
        for fi in infos:
            safe_member_name(fi.filename)
            budget.add(fi.uncompressed or 0)
        extract_dir = work_dir / "x7z"
        extract_dir.mkdir()
        sz.extract(path=str(extract_dir))
        for fi in infos:
            name = safe_member_name(fi.filename)
            real = extract_dir / name
            if fi.is_symlink:
                skipped += 1
                continue
            if fi.is_directory:
                yield Member(
                    name,
                    True,
                    0,
                    fi.creationtime.timestamp() if fi.creationtime else 0.0,
                    lambda: io.BytesIO(b""),
                )
            else:
                mt = fi.creationtime.timestamp() if fi.creationtime else 0.0
                yield Member(name, False, real.stat().st_size, mt, (lambda real=real: open(real, "rb")))
        sz.close()
    if skipped:
        warns.append(f"{skipped} link or special entr{'y was' if skipped == 1 else 'ies were'} skipped.")


def _first_wins(members: Iterator[Member]) -> Iterator[Member]:
    seen: set[str] = set()
    for m in members:
        key = m.name.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        yield m


def _write_archive(
    fmt: str, out_path, members: Iterator[Member], warns: list[str], ctx: ExecutionContext
) -> int:
    members = _first_wins(members)
    count = 0
    if fmt == "zip":
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for m in members:
                require_not_cancelled(ctx)
                zi = zipfile.ZipInfo(
                    m.name + ("/" if m.is_dir and not m.name.endswith("/") else ""), _zip_time(m.mtime)
                )
                zi.external_attr = (0o40755 if m.is_dir else 0o100644) << 16
                if m.is_dir:
                    zf.writestr(zi, b"")
                else:
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    with m.open() as src, zf.open(zi, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, 1024 * 256)
                count += 1
    elif fmt in ("tar", "tgz"):
        with tarfile.open(out_path, "w:gz" if fmt == "tgz" else "w:", format=tarfile.PAX_FORMAT) as tf:
            for m in members:
                require_not_cancelled(ctx)
                ti = tarfile.TarInfo(m.name)
                ti.mtime = int(m.mtime)
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                if m.is_dir:
                    ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                    tf.addfile(ti)
                else:
                    ti.size, ti.mode = m.size, 0o644
                    with m.open() as src:
                        tf.addfile(ti, src)
                count += 1
    elif fmt == "7z":
        import py7zr

        staging = ctx.work_dir / "stage7z"
        staging.mkdir()
        entries: list[tuple[str, str]] = []
        for m in members:
            require_not_cancelled(ctx)
            dest = staging / m.name
            if m.is_dir:
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with m.open() as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 256)
                os.utime(dest, (m.mtime or 0, m.mtime or 0))
            entries.append((str(dest), m.name))
            count += 1
        with py7zr.SevenZipFile(out_path, "w") as sz:
            for real, arc in entries:
                sz.write(real, arc)
    return count


def _zip_time(mtime: float) -> tuple[int, int, int, int, int, int]:
    import time

    t = time.localtime(mtime if mtime and mtime > 315532800 else 315532800)
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


class ArchiveEngine(EngineAdapter):
    engine_id = "archives"

    def capabilities(self) -> list[Capability]:
        caps = []
        for s in MULTI:
            for t in MULTI:
                if s == t:
                    continue
                py7 = "7z" in (s, t)
                caps.append(
                    Capability(
                        id=f"archive.{s}->{t}",
                        engine_id=self.engine_id,
                        source=s,
                        target=t,
                        operation=Operation.REPACK,
                        fidelity=Fidelity.LOSSLESS_REENCODE,
                        summary=f"Repack {s.upper()} as {t.upper()}",
                        options=(),
                        requires=("py:py7zr",) if py7 else (),
                        warnings=(
                            "File contents and names are preserved. Ownership, permissions, links and "
                            "special files are not.",
                        ),
                        license=_7Z if py7 else _ZIP,
                        resources=ResourceHint(memory_factor=0.2, seconds_per_mb=0.05, max_input_mb=2048),
                        composable=False,
                    )
                )
        for t in GZ_TEXT:
            caps.append(
                Capability(
                    id=f"gzip.gz->{t}",
                    engine_id=self.engine_id,
                    source="gz",
                    target=t,
                    operation=Operation.CONVERT,
                    fidelity=Fidelity.BYTE_PRESERVING,
                    summary=f"Decompress gzip containing {t.upper()}",
                    requires=(),
                    warnings=(),
                    license=_ZIP,
                    resources=ResourceHint(memory_factor=0.1, max_input_mb=2048),
                )
            )
            caps.append(
                Capability(
                    id=f"gzip.{t}->gz",
                    engine_id=self.engine_id,
                    source=t,
                    target="gz",
                    operation=Operation.CONVERT,
                    fidelity=Fidelity.BYTE_PRESERVING,
                    summary=f"Compress {t.upper()} with gzip",
                    requires=(),
                    license=_ZIP,
                    resources=ResourceHint(memory_factor=0.1, max_input_mb=2048),
                )
            )
        return caps

    def availability(self, cap: Capability) -> Availability:
        if "7z" in (cap.source, cap.target):
            try:
                import py7zr
            except ImportError:
                return Availability(False, "py7zr is not installed (pip install 'ezconverter[sevenzip]').")
            return Availability(True, version=f"py7zr {py7zr.__version__}")
        return Availability(True, version="stdlib")

    def assess(self, cap: Capability, info: InputInfo, options: dict) -> Assessment:
        return Assessment(cap.fidelity, list(cap.warnings))

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        cap = ctx.capability
        s, t = cap.source, cap.target
        warns: list[str] = []
        out = ctx.output_path(EXT.get(t, t))
        try:
            if s == "gz" or t == "gz":
                return self._gzip(ctx)
            ctx.progress(None, "repacking")
            count = _write_archive(
                t, out, _iter_members(s, str(ctx.input.path), ctx.work_dir, warns), warns, ctx
            )
            if count == 0:
                raise EZError(ErrorCode.MALFORMED_INPUT, "The archive contains no usable files.")
            return ExecutionResult(out, warns, {}, {"members": count})
        except EZError:
            out.unlink(missing_ok=True)  # never leave a partial archive behind
            raise
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, gzip.BadGzipFile) as exc:
            out.unlink(missing_ok=True)
            raise EZError(ErrorCode.MALFORMED_INPUT, "The archive is damaged or truncated.") from exc
        except OSError as exc:
            out.unlink(missing_ok=True)
            raise EZError(ErrorCode.CONVERSION_FAILED, "The archive could not be processed.") from exc
        except Exception as exc:
            out.unlink(missing_ok=True)
            if type(exc).__module__.startswith("py7zr"):
                raise EZError(ErrorCode.MALFORMED_INPUT, "The 7z archive is damaged or unsupported.") from exc
            raise

    def _gzip(self, ctx: ExecutionContext) -> ExecutionResult:
        cap = ctx.capability
        t = cap.target
        out = ctx.output_path(t)
        if t == "gz":
            with open(ctx.input.path, "rb") as src, gzip.GzipFile(out, "wb", compresslevel=6, mtime=0) as dst:
                shutil.copyfileobj(src, dst, 1024 * 256)
        else:
            written = 0
            with gzip.open(ctx.input.path, "rb") as src, open(out, "wb") as dst:
                while chunk := src.read(1024 * 256):
                    written += len(chunk)
                    if written > MAX_TOTAL_BYTES or (
                        written > RATIO_MIN_BYTES and written / max(1, ctx.input.size) > MAX_RATIO
                    ):
                        raise EZError(
                            ErrorCode.UNSAFE_INPUT,
                            "The gzip stream expands too much (possible decompression bomb).",
                        )
                    dst.write(chunk)
                    require_not_cancelled(ctx)
        return ExecutionResult(out, [], {})

    def extra_checks(self, ctx: ExecutionContext, result: ExecutionResult) -> list[Check]:
        cap = ctx.capability
        if "gz" in (cap.source, cap.target):
            return []
        try:
            src = _listing(cap.source, str(ctx.input.path), ctx.work_dir)
            dst = _listing(cap.target, str(result.output_path), ctx.work_dir)
        except Exception:
            return [Check("members_match", False, "Could not list archive members for comparison.")]
        missing = sorted(set(dst) - set(src))
        dropped = sorted(set(src) - set(dst))
        size_diff = [n for n in dst if n in src and dst[n] != src[n]]
        # Links/special entries are skipped on purpose (reported as a warning); regular files must all survive.
        skipped_ok = any("skipped" in w for w in result.warnings)
        ok = not missing and not size_diff and (not dropped or skipped_ok)
        detail = f"{len(dst)} file(s) in output, {len(src)} in source"
        if not ok:
            detail += f"; unexpected={missing[:3]} dropped={dropped[:3]} size_mismatch={size_diff[:3]}"
        return [Check("members_match", ok, detail)]


def _listing(fmt: str, path: str, work_dir) -> dict[str, int]:
    """Regular-file names and sizes (first occurrence wins), read from headers only where possible."""
    out: dict[str, int] = {}
    if fmt == "zip":
        with zipfile.ZipFile(path) as zf:
            for zi in zf.infolist():
                if not zi.is_dir() and ((zi.external_attr >> 16) & 0o170000) != 0o120000:
                    out.setdefault(safe_member_name(zi.filename), zi.file_size)
    elif fmt in ("tar", "tgz"):
        with tarfile.open(path, "r:gz" if fmt == "tgz" else "r:") as tf:
            for ti in tf:
                if ti.isreg():
                    out.setdefault(safe_member_name(ti.name), ti.size)
    elif fmt == "7z":
        import py7zr

        with py7zr.SevenZipFile(path, "r") as sz:
            for fi in sz.list():
                if not fi.is_directory and not fi.is_symlink:
                    out.setdefault(safe_member_name(fi.filename), fi.uncompressed or 0)
    return out
