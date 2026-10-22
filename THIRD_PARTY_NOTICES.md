# Third-party notices

EZConverter itself is licensed under the [Apache License 2.0](LICENSE) (Copyright 2026 SamsonEnochRepos, see [NOTICE](NOTICE)).
It relies on, and the Windows installer redistributes, third-party software under separate licenses. Those licenses apply to those
components only; they do not change the license of EZConverter's own source code.

This file is an engineering inventory, not legal advice. The machine-generated, per-package list is in
[docs/security/license-inventory.md](docs/security/license-inventory.md).

## Conversion engines bundled in the Windows installer

The engines are separate executables started as child processes. EZConverter does not link against them. They are shipped unmodified,
exactly as published by the projects below, and the installer contains each project's license text under `resources/licenses/`.

| Component | Version | License | Binary downloaded from | Source code |
|---|---|---|---|---|
| FFmpeg (gyan.dev "essentials" build; enables libx264 and libx265, so the build is GPL) | 9.0.2 | GPL-3.0-or-later | https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip | https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz and https://www.gyan.dev/ffmpeg/builds/ |
| Pandoc | 3.12.1 | GPL-2.0-or-later | https://github.com/jgm/pandoc/releases/tag/3.12.1 | https://github.com/jgm/pandoc/tree/3.12.1 |
| Poppler (`pdftoppm`, `pdfinfo`) with its bundled libraries | 26.09.0-0 | GPL-2.0-or-later (Poppler); bundled libraries under their own licenses | https://github.com/oschwartz10612/poppler-windows/releases/tag/v26.09.0-0 | https://gitlab.freedesktop.org/poppler/poppler and https://github.com/oschwartz10612/poppler-windows |

The exact archives and their SHA-256 checksums are pinned in [scripts/engines.lock.json](scripts/engines.lock.json); the installer build
refuses to bundle a file whose checksum differs.

**Written offer.** For at least three years after you receive an installer, you may request the complete corresponding source code of any
of the GPL components above, for the cost of physically performing the distribution, by opening an issue at
https://github.com/SamsonEnochRepos/EZConverter/issues. The upstream locations in the table already provide the exact versions.

**LibreOffice** (MPL-2.0 and others) is *not* bundled. EZConverter detects an existing installation and runs it as a separate process
for Word, Excel, PowerPoint and OpenDocument conversions.

## Python runtime bundled in the converter sidecar

The sidecar (`resources/sidecar`) is a PyInstaller build of `backend/`. It contains CPython (PSF-2.0) and the packages below.
Versions are those resolved at build time; see the license inventory for exact numbers.

| Package | License |
|---|---|
| FastAPI, Starlette, Pydantic, annotated-types, typing-extensions | MIT / BSD-3-Clause / PSF-2.0 |
| Uvicorn, h11, anyio, click, idna | BSD-3-Clause / MIT |
| Pillow (with libwebp, libtiff, libjpeg-turbo, zlib, and others) | MIT-CMU (HPND); bundled libraries are permissive |
| pypdf | BSD-3-Clause |
| PyYAML, defusedxml | MIT / PSF-2.0 |
| py7zr (7z archives) and its dependencies `pybcj`, `pyppmd`, `inflate64`, `multivolumefile` | **LGPL-2.1-or-later** |
| brotli, pycryptodomex, texttable, psutil, backports.zstd | MIT / BSD / Public Domain / PSF-2.0 |

**LGPL components (py7zr family).** These are included unmodified. To replace one with a modified version, rebuild the sidecar from source:
install your modified package into the build environment and run `python scripts/build_sidecar.py` (see [CONTRIBUTING.md](CONTRIBUTING.md)).
Source: https://github.com/miurahr/py7zr and the other projects' PyPI/GitHub pages.

## Desktop shell

The desktop app is built on Electron (MIT) and Chromium (BSD-3-Clause and many bundled components). Electron's installer includes
`LICENSE.electron.txt` and `LICENSES.chromium.html` next to the executable; keep them when redistributing.

## Mobile app and web app

Built with React, React Native, Expo, Vite and Tailwind CSS (MIT), and their dependencies (predominantly MIT, ISC, BSD and Apache-2.0).
The per-package list is generated into [docs/security/license-inventory.md](docs/security/license-inventory.md).

## Trademarks

FFmpeg, Pandoc, Poppler, LibreOffice, Electron, Expo, Android and Windows are trademarks of their respective owners. EZConverter is an
independent project and is not affiliated with or endorsed by them.
