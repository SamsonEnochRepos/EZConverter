#!/usr/bin/env python
"""Download the Windows conversion engines that the desktop installer bundles, verify their SHA-256
against scripts/engines.lock.json, and flatten them into apps/desktop/engines/ together with their
license texts. Run on Windows before `pnpm --filter @ezconverter/desktop dist`.

    python scripts/fetch_windows_engines.py [--cache DIR]

The engines are separate executables (never linked into EZConverter). FFmpeg, Pandoc and Poppler are
GPL; see THIRD_PARTY_NOTICES.md for the source offer that ships with the installer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = json.loads((ROOT / "scripts" / "engines.lock.json").read_text(encoding="utf-8"))
OUT = ROOT / "apps" / "desktop" / "engines"
# Executables (and their DLLs) copied from each archive; everything else is dropped.
WANTED_EXE = {
    "ffmpeg": {"ffmpeg.exe", "ffprobe.exe"},
    "pandoc": {"pandoc.exe"},
    "poppler": {"pdftoppm.exe", "pdfinfo.exe"},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str, spec: dict, cache: Path) -> Path:
    dest = cache / spec["file"]
    if not (dest.exists() and sha256(dest) == spec["sha256"]):
        print(f"downloading {name} {spec['version']} ...")
        req = urllib.request.Request(spec["url"], headers={"User-Agent": "EZConverter-build"})
        with urllib.request.urlopen(req) as r, dest.open("wb") as fh:  # nosec B310 - pinned https URL
            shutil.copyfileobj(r, fh)
    got = sha256(dest)
    if got != spec["sha256"]:
        dest.unlink()
        sys.exit(f"CHECKSUM MISMATCH for {name}: expected {spec['sha256']}, got {got}. Refusing to bundle it.")
    print(f"  {name}: sha256 verified")
    return dest


def safe_members(z: zipfile.ZipFile):
    for info in z.infolist():
        parts = Path(info.filename).parts
        if info.filename.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
            sys.exit(f"unsafe path in archive: {info.filename}")
        yield info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=ROOT / "apps" / "desktop" / ".engine-cache")
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "licenses").mkdir(parents=True)

    for name, spec in LOCK.items():
        archive = download(name, spec, args.cache)
        with zipfile.ZipFile(archive) as z:
            members = list(safe_members(z))
            exe_dirs = {Path(m.filename).parent for m in members if Path(m.filename).name in WANTED_EXE[name]}
            if not exe_dirs:
                sys.exit(f"{name}: expected executables not found in {archive.name}")
            bin_dir = sorted(exe_dirs, key=lambda p: len(p.parts))[0]
            for m in members:
                p = Path(m.filename)
                if m.is_dir():
                    continue
                if p.parent == bin_dir and p.suffix.lower() in {".exe", ".dll"}:
                    # archives ship extra tools (ffplay, pdftotext, ...): keep only the executables we call, plus every DLL
                    if p.suffix.lower() == ".exe" and p.name not in WANTED_EXE[name]:
                        continue
                    (OUT / p.name).write_bytes(z.read(m))
                elif p.name.upper().startswith(("LICENSE", "COPYING", "README")) and p.parent.name != "share":
                    (OUT / "licenses" / f"{name}-{p.name}").write_bytes(z.read(m))
        (OUT / "licenses" / f"{name}-SOURCE.txt").write_text(
            f"{name} {spec['version']}\nBinary: {spec['url']}\nSource: {spec['source']}\nLicense: {spec['license']}\n",
            encoding="utf-8",
        )
    print(f"engines staged in {OUT}:")
    for f in sorted(OUT.glob("*.exe")):
        print(f"  {f.name}  {f.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
