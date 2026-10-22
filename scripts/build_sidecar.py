#!/usr/bin/env python
"""Freeze the converter backend into apps/desktop/sidecar/ with PyInstaller (one-folder build).

    python -m pip install pyinstaller "backend[sevenzip]"
    python scripts/build_sidecar.py

Produces apps/desktop/sidecar/ezconverter-server(.exe), which the Electron app launches as `serve --sidecar`
and which re-invokes itself as `worker <request.json>` for every sandboxed conversion.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "apps" / "desktop" / "sidecar"
WORK = ROOT / "apps" / "desktop" / ".pyinstaller"


def main() -> int:
    conda_bin = Path(sys.base_prefix) / "Library" / "bin"  # conda keeps libffi/openssl/etc. here; PyInstaller must see them
    if sys.platform == "win32" and conda_bin.is_dir():
        os.environ["PATH"] = f"{conda_bin}{os.pathsep}{os.environ['PATH']}"
    for d in (OUT, WORK):
        shutil.rmtree(d, ignore_errors=True)
    entry = WORK / "entry.py"
    WORK.mkdir(parents=True)
    entry.write_text("from ezconverter.__main__ import main\nraise SystemExit(main())\n", encoding="utf-8")
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "ezconverter-server",
        "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "build"), "--specpath", str(WORK),
        "--paths", str(ROOT / "backend"),
        "--collect-submodules", "ezconverter", "--collect-data", "ezconverter",
        "--collect-submodules", "uvicorn", "--collect-submodules", "py7zr",
        "--hidden-import", "defusedxml", "--hidden-import", "yaml",
        str(entry),
    ]
    subprocess.run(cmd, check=True)
    shutil.copytree(WORK / "dist" / "ezconverter-server", OUT)
    shutil.rmtree(WORK, ignore_errors=True)
    exe = OUT / ("ezconverter-server.exe" if sys.platform == "win32" else "ezconverter-server")
    print(f"sidecar built: {exe} ({sum(f.stat().st_size for f in OUT.rglob('*') if f.is_file()) // 2**20} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
