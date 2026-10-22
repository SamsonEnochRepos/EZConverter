# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Open-source release material: Apache-2.0 `LICENSE`, `NOTICE` (creator attribution), `THIRD_PARTY_NOTICES.md` (bundled GPL/LGPL engines with source offer),
  `SECURITY.md`, `CONTRIBUTING.md`, a new README with downloads, screenshots and an honest platform matrix, and `docs/security/RELEASE_AUDIT.md`.
- Windows desktop packaging: PyInstaller converter sidecar (`scripts/build_sidecar.py`), checksum-pinned FFmpeg/Pandoc/Poppler bundle
  (`scripts/fetch_windows_engines.py`, `scripts/engines.lock.json`), NSIS installer and portable zip via electron-builder, app icon, and a packaged-sidecar smoke test (`scripts/smoke_packaged.py`).
- Android release build: signed APK through a config plugin (`apps/mobile/plugins/with-release-hardening.js`) that also restricts cleartext traffic to loopback;
  unneeded permissions are blocked; app icon and adaptive icon.
- Windows sandbox: workers run in a Job Object (memory, CPU-time and process-count limits, UI restrictions, whole-tree kill, fail-closed) with tests in `tests/security/test_windows_job.py`.
- Packaged builds can run workers through the converter executable itself (`worker` command) and find bundled engines via `EZ_ENGINE_DIR`.
- App icon (Windows installer, Android launcher and adaptive icon, web favicon) and a `.dockerignore`; the production Dockerfile is now built and exercised.
- `apps/desktop/e2e/packaged.spec.ts`: e2e for the packaged Windows app over Chromium's remote-debugging port (the release build disables `--inspect`); run with `EZ_DESKTOP_EXE`.
- `EZ_CXX_STAGING_DIR` support in the Android config plugin to keep CMake paths under Windows' 260-character limit.
- UI redesign for web, desktop and mobile: royal-blue hero, rounded cards, pill buttons, file tiles with a format badge,
  a source-to-target pill on each file, a circular progress ring and headlines with an italic serif accent word.
- Web and desktop shell: left sidebar on wide screens and a floating bottom navigation on phones.
- Mobile: hero header, "Batch conversions" and "Conversions history" tiles, a Recent list, a bottom sheet for formats,
  and a dependency-free `ProgressRing` and `FileTile`.
- New shared components in `packages/ui`: `FileTile`, `ProgressRing` and a `hero` button variant.
- README screenshots in `docs/screenshots/` (web light and dark, phone width, native Android, and `overview.png`).
- Release verification, recorded in `docs/security/RELEASE_AUDIT.md`: signed Android APK verified with `apksigner` and run end to end on an emulator against the production container; Windows installer fuses and `app.asar` inspected; production Docker image built and run read-only with dropped capabilities.
- Packaged Windows layout is checked with `scripts/smoke_packaged.py`; release files are staged with a `SHA256SUMS.txt` (not committed).

### Changed
- Design tokens (light and dark palettes, radius, shadows) in `packages/ui/src/styles.css` and `theme.css`; mobile `theme.ts` now exposes `dark`, `hero` and `shadow`.
- Selects are constrained to their container so long option labels no longer cause horizontal scrolling on phones.
- `tests/fixtures/generate.py` uses a cross-platform file lock (`fcntl` on POSIX, `msvcrt` on Windows) so the Python tests can load on Windows.
- `.gitignore` now ignores `.ruff_cache/`, `*.log`, editor folders and the `/zips/` archive folder.

### Security
- LibreOffice fetched remote resources (linked images, stylesheets) from imported HTML whenever the host had no network namespace (Windows, default Docker): its HTTP/HTTPS/FTP traffic is now forced to a dead local proxy, covered by a test that saw 19 outbound requests before the fix and 0 after.
- The production Dockerfile upgrades pip inside the image (Ubuntu's venv ships pip 24.0 with 12 known advisories); the image now builds and runs under a read-only root with all capabilities dropped.
- Repository hygiene: the project archive (`zips/`) and `*.egg-info` are no longer tracked; signing material patterns are git-ignored.

### Changed
- `docs/STATUS.md` now describes the real state of the Windows, Android and Docker builds instead of "never built".
- `docs/security/RELEASE_AUDIT.md` and `docs/STATUS.md` record the packaged-app e2e outcome: conversions pass, the "quitting stops the converter" test timed out and is unresolved.
- Package metadata (`pyproject.toml`, every `package.json`) now declares `Apache-2.0` and the creator instead of "Proprietary"; the README was rewritten (the old one ended in corrupted UTF-16 text).
- Reviewed SQL builders in `orchestration/store.py` carry `# nosec B608` with the reason; Bandit reports 0 medium/high findings.

### Fixed
- PDF conformance check reads text with `pdftotext -raw` and ignores whitespace, so LibreOffice wrapping a word after a hyphen no longer fails presentation to PDF cases.
- Desktop and shared-domain test suites no longer fail on Windows because of hard-coded POSIX paths or a data directory deleted while the server still held it open.
- Image engine: EXIF Orientation is reset to 1 when the pixels were already rotated, so phone photos are no longer rotated twice.
- Sandbox: on POSIX the worker PATH now also exposes the directories of the resolved engine tools (ffmpeg, pandoc, poppler, soffice), so conda/Homebrew installs work inside the worker; the rest of the environment stays scrubbed.
- LibreOffice engine and test fixtures: the `-env:UserInstallation` argument is a proper `file:///` URI (valid on Windows).
- Job manager: a normal shutdown re-queues in-flight jobs instead of marking them CANCELLED, and a cancel that arrives between claim and worker start is no longer lost.
- Archives: 7z members are spooled instead of extracted to disk (symlinks and special entries skipped, duplicate names resolved first-wins like ZIP); corrupt gzip/tgz data is reported as non-retryable `malformed_input` instead of an internal error.
- API: a non-ASCII signature on a signed download link returns 404 instead of a 500.
- Shared batch controller: stale in-flight plans can no longer overwrite newer options, double-clicking Convert submits each file once, and a failing later group no longer strands runs that already started.
- Mobile unit tests no longer fail on import: the serif font is chosen lazily from `Platform.OS` instead of calling `Platform.select` at module load.

### Verified (Windows and Android emulator)
- Typecheck, UI, web and mobile unit tests, web e2e, desktop (Electron) e2e, mobile web e2e and native iOS/Android bundles run on Windows;
  the native Android app was exercised on the Pixel 9 Pro emulator (connect, pick, convert, verify, share, history).
- Regression tests added for every fix above (Python 130 -> 159 passing, shared-domain 23 -> 27); no new failures against the Windows baseline.
- Known Windows environment gaps (not UI defects): LibreOffice, ImageMagick and the libmp3lame/libvpx/libvorbis ffmpeg encoders are missing,
  several tests assume Unix paths and `prlimit`, and `scripts/check-contracts.mjs` cannot spawn `.bin/openapi-typescript` on Windows.
