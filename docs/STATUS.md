# Implementation status

_First engineering pass, updated for the 0.1.0 pre-release (2026-10-22). Detailed pre-release evidence, including what was **not** tested, is in [security/RELEASE_AUDIT.md](security/RELEASE_AUDIT.md)._

## Summary

| Area | Status |
|---|---|
| Backend conversion core (formats, detection, registry, planner, 7 engines, verification, sandboxed workers) | Implemented and tested against real engines |
| API (22 paths, OpenAPI, idempotency, auth, rate limits, signed downloads) | Implemented and tested |
| Orchestration (durable queue, bounded concurrency, cancel, retry, recovery, batches + ZIP/manifest, retention) | Implemented and tested |
| Web app (home, workspace, batch, results, history, settings) | Implemented; Chromium e2e on real backend |
| Desktop (Electron) | Implemented; Windows installer + portable zip built (unsigned); packaged converter smoke-tested; one packaged-app e2e run converted files successfully but its "quitting stops the converter" test timed out (unresolved), and the final installer was not re-run through it |
| Mobile (Expo/React Native) | Implemented; signed Android release APK built and run end to end on an Android 17 **emulator** (not a physical device); iOS not built |
| Hybrid local/remote policy | Implemented in server, router and controller; tested incl. remote-location server |
| Hardening / docs / licensing inventory | Done; see blockers below |

## Verification evidence (executed)

| Check | Result |
|---|---|
| Python lint (ruff) | pass |
| Python unit + integration + security tests | **155 passed** |
| **Conformance: every advertised capability on a real input with the real engine, outputs checked independently** | **321 / 321 passed** (0 failing, 0 untested) |
| Generated TS contracts match the backend | in sync |
| TypeScript typecheck (contracts, api-client, shared-domain, ui, web, desktop, mobile) | pass |
| TypeScript unit/integration tests | **74 passed** (api-client 6 and shared-domain 25 run against the live Python backend; desktop 21; mobile 7; ui 12; web 3) |
| Web e2e (real Chromium + built app + real backend; includes phone viewport) | **8 passed** |
| Desktop e2e (real Electron under Xvfb: hardening, IPC, CSP, traversal, sidecar auth, a real conversion, sidecar shutdown) | **7 passed** |
| Mobile UI e2e (react-native-web in Chromium against a **remote-location** backend: consent gate, nothing uploaded without consent / under Local only, convert + verified export) | **5 passed** |
| Mobile native bundles (Hermes, iOS + Android) | compile |
| `pip-audit` | no known vulnerabilities |
| `pnpm audit --prod` | 4 findings in Expo build tooling only (see below) |

### Conversions verified with real input files (321 direct capabilities)

Images 64 (PNG/JPEG/WebP/AVIF/GIF/TIFF/BMP/ICO, incl. same-format resize/compress); audio 64 + video→audio 48; video 36 (incl. stream-copy remux); documents via LibreOffice 63
(DOCX/DOC/ODT/RTF/TXT/HTML ↔, spreadsheets, presentations, → PDF, SVG → PNG/PDF) and Pandoc 10 (Markdown ↔ HTML/DOCX/ODT/RTF/TXT, …); PDF → TXT and PDF → PNG/JPEG/TIFF 4; archives 26
(ZIP/TAR/TGZ/7Z repack, gzip ↔ text/data); structured data 6 (JSON ↔ YAML/CSV/XML). Multi-step routes (for example DOCX → PNG via PDF, YAML → CSV via JSON, Markdown → PDF) were verified end to end through the API.
Full table: [format matrix](format-matrix/README.md), machine-readable: [capabilities.json](format-matrix/capabilities.json).

### Platforms actually tested

Linux x86-64 only: backend + engines, Chromium (web and mobile-preview), Electron under Xvfb. **Not tested:** Windows, macOS, iOS, Android, Firefox/Safari.

## What was NOT verified (executed vs. only written)

| Area | State |
|---|---|
| Windows desktop | Built and checked headlessly (fuses, asar, bundled converter and engines). Workers get a Job Object but **no network isolation**. macOS: **not built or run**. |
| Desktop installers | Windows NSIS installer and portable zip built; fuses read back from the binary. Code signing, notarisation (macOS) and auto-update: **not done**. |
| iOS / Android | Android: release APK signed, installed and used on an emulator against the production container (connect, pick, convert, verified result). Physical devices and iOS: **not run.** Previously: Verified: TypeScript, Hermes bundles for both platforms compile, platform-adapter unit tests (mocked native modules), and the same React Native UI running in Chromium via react-native-web against a real remote-location backend. Real file-provider URIs, `XMLHttpRequest` file-URI streaming, `expo-file-system` downloads, share sheets, keychain and app suspension/resume are **untested**. |
| Container image | Dockerfile built and run with a read-only root, dropped capabilities and no-new-privileges: health, auth, Pandoc and LibreOffice conversions work. The compose file and Kubernetes manifests were **not** run. |
| GitHub Actions workflow | Written, **never executed** on GitHub. `scripts/verify.sh` and the Docker-based run in the release audit are its executed equivalents. |
| PostgreSQL, a distributed queue, object storage | **Not implemented.** Single-node SQLite + local filesystem behind `Store` / `LocalArtifactStore`. |
| Metrics/tracing exporters | **Not implemented** (structured logs, request ids and the job audit table only). |
| In-browser (WASM) local conversion | **Not implemented.** The web app uses a local or remote converter over HTTP. |
| Load / soak / fuzz testing | **Not done.** Only targeted bomb/abuse cases (see security tests). |
| Accessibility audit with real assistive tech | **Not done.** Semantics, keyboard operation and touch-target size are asserted in tests; no screen-reader pass. |
| Large-file behaviour (GB-scale video/archives) | Streaming is used for uploads/archives/downloads, but nothing near the limits was exercised. |

## Known limitations of the product (by design or not yet built)

* No OCR; scanned PDFs cannot become text. No PDF → editable document conversion. Raster → SVG is not offered. HEIC/HEIF is recognised, not decoded. No video-frame/thumbnail extraction.
* PDF → image renders **one page** (option `page`). Multi-page output as a ZIP of images is not implemented.
* Spreadsheet → CSV exports only the first sheet. Pandoc-based Markdown conversions drop images and raw HTML/LaTeX on purpose.
* Progress is real only for FFmpeg (fractional) and the test engines; Pillow, LibreOffice, Pandoc, archives and structured data report *indeterminate* progress by design.
* Conversions are not resumable mid-file: after a crash the attempt restarts from the original upload (bounded by `EZ_MAX_ATTEMPTS`).
* Custom presets can be stored (`presetsFor(custom)`), but there is no UI to create/save them yet; only built-in presets are offered. Retention is configured on the server and *shown* in the apps (not user-editable).
* Mobile cannot run engines locally: `LOCAL_ONLY` on a phone refuses with an explanation. Batches that span two runtimes yield one ZIP per runtime.
* Additional families named in the brief (ebooks, fonts, CAD, 3D, scientific data) have no adapters yet; the adapter contract and `EZ_EXTRA_ENGINES` plugin hook are in place.
* Archive support: ZIP, TAR, TAR.GZ, 7Z (needs `py7zr`), single-file gzip for text/data. No tar.xz/bz2, RAR, or ISO.

## Findings from testing that were fixed during development

These were found *by the tests* (not by inspection) and are now covered by regression tests:
`yaml→json` alias bomb exhausted 5 GB before the limit stopped it (now a node budget); XML/SVG with a `DOCTYPE` was misdetected as text; rejected uploads left empty
directories; Pandoc emitted RTF fragments and could embed local files referenced from Markdown; MPEG-PS → MKV stream-copy failed (timestamps); archive adapter had a py7zr API mismatch
and left partial output on failure; the web UI let "Convert" run with *stale options* inside a debounce window; the saved default processing mode was ignored after load; intake uploaded files
under `LOCAL_ONLY` when remote consent was on; the router and API used different error codes for the same condition; network isolation could degrade silently; `URL.origin` differs for custom schemes
between Node and Chromium (desktop sender check).

## Dependency audit

* `pip-audit`: no known vulnerabilities.
* `pnpm audit --prod`: 4 findings (2 high, 2 moderate) — all transitive dependencies of **Expo's build tooling** (`@expo/cli → node-forge`, `metro-file-map → micromatch → braces`,
  `@expo/config-plugins → xcode → uuid`); none is included in the shipped app bundles. No patched release exists for two of them. Accepted for now, to be re-checked on each Expo upgrade.
* Licensing: see [license inventory](security/license-inventory.md). **Release blockers:** the FFmpeg build used here is GPL (libx264/x265); Pandoc and Poppler are GPL executables; py7zr is LGPL.

## Unresolved risks and release blockers

1. **Licensing/redistribution sign-off** for the bundled engine set (GPL FFmpeg/Pandoc/Poppler, LGPL py7zr, LibreOffice fonts) before any packaged desktop/mobile/container distribution.
2. **macOS packaging and cross-OS sandboxing**: Windows workers have resource limits but no network isolation; macOS has neither.
3. **Mobile device validation** on physical Android devices and on iOS (file providers, large uploads over cellular, suspension, share/export).
4. **Multi-node scale-out**: SQLite/local-disk queue is single-node; PostgreSQL + object storage + a real queue are needed for horizontal scaling and HA.
5. **Auth** is static tokens; no accounts, SSO, rotation or scopes. TLS/at-rest encryption are deployment responsibilities.
6. **Stronger isolation for a public service** (gVisor/Firecracker/containers with egress denied, cgroup quotas, malware scanning).
7. **Observability** exporters and alerting are not built.
8. Engine upgrades can change outputs: re-run conformance (`scripts/verify.sh`) on every engine bump; the manifest records the engine versions it was generated against.

## Next implementation tasks

1. Windows and macOS: code signing and notarisation, auto-update, network isolation for workers (for example an AppContainer or firewall rule).
2. PostgreSQL `Store` + object-storage `ArtifactStore`, queue worker separation, metrics.
3. Package the sidecar (PyInstaller + engines) and build/sign desktop installers on Windows/macOS/Linux CI; add Windows/macOS job sandboxing (Job Objects / `sandbox-exec`/App Sandbox).
4. Device-test the mobile app (Maestro/Detox), then iOS/Android builds with EAS.
5. Coverage expansion in value order: multi-page PDF→images ZIP, tar.xz/bz2, HEIC (after licensing review), video thumbnails, OCR (Tesseract) as an opt-in engine, ebook/font adapters.
6. Preset editor + saved presets; per-conversion advanced settings presets shared across apps.
7. Accessibility audit with VoiceOver/TalkBack/NVDA; i18n.
