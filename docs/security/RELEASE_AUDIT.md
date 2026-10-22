# Release audit: EZConverter 0.1.0 (pre-release)

Date: 2026-10-22. Scope: the source tree on `main` plus the uncommitted release changes, and the three release artifacts built from it.
This is an engineering audit done by the maintainer's tooling and an AI assistant. It is **not** an independent penetration test or a legal review.
Statements below say "passed" only for things that were actually run; everything else is listed under [Not tested](#not-tested-or-not-covered).

## Release artifacts

| File | Size | SHA-256 |
|---|---|---|
| `EZConverter-Setup-0.1.0-win-x64.exe` | 232 MiB | `8fb2cc8482fa7ecaccfff4d8d4790521b8f5f49b31961fa445abe6de071e9852` |
| `EZConverter-0.1.0-win-x64-portable.zip` | 317 MiB | `fc3a6668194df014a42b5061ac66ea3387434f6c79c14e94d82bcf0d76961f79` |
| `EZConverter-0.1.0-android.apk` | 49 MiB | `e5666738926151fb59ff7b4ae87a747e4677873b66888cb8dff9a87a8b7fe3be` |

Android signing certificate (SHA-256): `9C:80:36:4A:DE:EA:21:77:82:DE:E9:ED:B1:66:FC:68:D0:36:E2:F4:3C:3C:C6:4C:34:5F:B1:6C:2A:C6:07:45`
(RSA 4096, `CN=SamsonEnochRepos, O=EZConverter`). The Windows installer is **not code-signed** (`Get-AuthenticodeSignature` reports `NotSigned`).

## What was run and the result

| Check | Environment | Result |
|---|---|---|
| Python lint (`ruff`) | Windows and Linux | Pass |
| Python unit + integration + security tests | Linux (Ubuntu 24.04 container, default seccomp) | **182 passed, 8 skipped, 0 failed.** Skips: 6 Windows-only Job Object tests, 2 that need network namespaces (unavailable in a default container) |
| Conformance: every advertised capability converted from a real input with the real engine, output checked independently | Linux, single pass | **321 / 321 passed** |
| Windows sandbox tests (`test_windows_job.py`) | Windows 10 | 6 passed; the memory and CPU cases were confirmed to **fail** when the confinement is switched off |
| TypeScript typecheck | Windows, all workspace packages | Pass |
| TypeScript tests | Windows | 78 passed: ui 12, web 3, mobile 7, desktop 21, api-client 6, shared-domain 29 (two shared-domain cases need an FFmpeg with libvpx; they pass with the bundled FFmpeg) |
| Web e2e (real Chromium, real backend) | Windows | 6 passed, 2 failed: both are the "DOCX to PDF" flow and need LibreOffice, which is not installed on the test machine |
| Static analysis (Bandit) | `backend/ezconverter`, 6.6k lines | 0 high, 0 medium, 19 low (assert use, argv-list subprocess calls, a deliberate `except: pass` around a log callback). The 3 SQL-construction findings were reviewed: column names are code literals, all values are bound parameters; marked `# nosec B608` with the reason |
| Python dependency audit (`pip-audit`) | Windows build env | No known vulnerabilities. (A Linux test image reported 12 advisories in `pip 24.0`; the production Dockerfile had the same pattern and now upgrades pip first. The image runs pip 26.2.2.) |
| JavaScript dependency audit (`pnpm audit`) | Workspace | 4 findings (2 high, 2 moderate), all reached only through Expo's **build/CLI tooling** (`node-forge`, `braces`, `uuid`, `sprintf-js`); 3 are in the workspace's production dependency paths, but a search of the release APK's JavaScript bundle found no reference to any of these package names (supporting evidence only: the bundle is compiled Hermes bytecode). No patched release exists for two of them. Re-check on each Expo upgrade |
| Secret scan | Working tree, both git commits | No private keys, cloud/API token patterns or credentials found. `.env` is git-ignored and was never committed; the only `.env`-like file in history is `.env.example`. The scan was regex-based, not a dedicated scanner such as gitleaks |
| Windows packaged converter | Built installer layout (`resources/sidecar` + `resources/engines`) | Headless smoke test passed: 258 capabilities advertised; real PNG to WebP, JSON to YAML, WAV to MP3 (bundled FFmpeg) and Markdown to HTML (bundled Pandoc), each through a Job Object-confined `worker` process |
| Packaged Windows app, e2e (single earlier run) | `win-unpacked/EZConverter.exe` over the debugging port | Renderer isolation, token/capability and PNG to WebP plus MP3 to Opus conversions did not fail and results were marked Verified; the "quitting stops the converter" test **timed out (unresolved, see below)** |
| Windows app hardening | `EZConverter.exe` binary and `app.asar` | Electron fuses read back: RunAsNode, NODE_OPTIONS and `--inspect` disabled; cookie encryption, ASAR integrity and only-load-from-ASAR enabled. The asar holds only the app's own `dist/` (current code) |
| Android APK | `apksigner`, `aapt2` | Signature verifies (v2 scheme); signer matches the fingerprint above; permissions are `INTERNET`, `USE_BIOMETRIC`, `USE_FINGERPRINT` (secure storage) only; not debuggable; ABIs arm64-v8a, armeabi-v7a, x86_64; min SDK 24, target SDK 36 |
| Android runtime | Pixel 9 Pro **emulator**, Android 17 | Installed, launched, connected to the production container over loopback HTTP (allowed only for loopback), authenticated, picked a file from the system chooser, refused to upload until remote processing was enabled, then converted PNG to WebP; result shown as Verified. The app process was killed once by the emulator updating WebView (`am_kill ... killDueToPackageUpdate`), not by a crash |
| Production container | Docker, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, pids and memory limits | Built; `/v1/health` ok; 321 capabilities; unauthenticated request rejected (401); remote consent enforced (409 until the client sets the execution policy); Markdown to DOCX (Pandoc) and DOCX to PDF (LibreOffice) succeeded; no errors in the container log |

## Findings and what was done

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | **LibreOffice fetched remote resources** (linked images and stylesheets from imported HTML) whenever the host had no network namespace: Windows, and default Docker. A test saw 19 outbound requests. Impact: tracking beacons and probes of local services by a hostile document | High on Windows/Docker | **Fixed.** LibreOffice's HTTP, HTTPS and FTP traffic is forced to a dead local proxy with an empty bypass list. The test now also covers HTTPS and FTP and sees 0 requests; reverting the fix makes it fail again |
| 2 | Windows workers had **no resource limits** (no `prlimit`) and were killed by `taskkill` only | High for the desktop app | **Fixed.** Job Object: committed-memory and CPU-time caps, at most 16 processes, no desktop/clipboard/system-parameter access, whole-tree termination, created suspended and resumed only once confined, fail-closed |
| 3 | Windows workers still have **no network isolation** | Medium, inherent | **Open, documented.** Stated in the README, `SECURITY.md` and the security model. Mitigated for LibreOffice by finding 1; FFmpeg, Pandoc and Poppler can still open sockets if exploited |
| 4 | The project archive `zips/EZConverter.zip` and `*.egg-info` were tracked in git | Low | Untracked and ignored. The archive **remains in git history**; rewriting history is the owner's decision |
| 5 | Release builds of the Expo app were debug-signed and requested overlay (`SYSTEM_ALERT_WINDOW`), storage and vibrate permissions the app does not need | Medium | **Fixed.** Release-key signing through an environment-driven config plugin and the unneeded permissions blocked. A network security config keeps Android's default of blocking cleartext HTTP and permits it only for loopback (`localhost`, `127.0.0.1`, `10.0.2.2`) so local testing works |
| 6 | Production Dockerfile shipped `pip 24.0` with 12 known advisories | Low | **Fixed** (pip upgraded in the image) |
| 7 | Test-suite defects: POSIX path assumptions in desktop tests, a data-dir teardown race on Windows, and a PDF text check broken by `pdftotext` de-hyphenation | n/a | Fixed |

## Not tested or not covered

* **No independent security review, fuzzing campaign or penetration test.** Only targeted abuse cases (bombs, traversal, injection, SSRF-style fetches) exist.
* **Windows network isolation and any VM-level sandbox**: not implemented. A kernel or engine exploit is out of scope for the process-level sandbox.
* **The packaged Windows app has only a partial, single-run e2e result.** `apps/desktop/e2e/packaged.spec.ts` ran once against `release/win-unpacked/EZConverter.exe` (13:40, before the installer was rebuilt with the final sidecar and icon). Its image and audio conversions completed and were marked Verified, and the desktop screenshots in `docs/screenshots/desktop-*.png` come from that run. Only the last test, "quitting the app stops the converter", is recorded as failed: it timed out after 120 s. That is **unresolved**: it may be a flaw in how the test closes the app over the debugging port, or a real problem with the converter not stopping on quit. It has not been investigated or re-run, and the rebuilt installer has not been through this e2e at all. Process listing afterwards showed no leftover EZConverter or converter process.
* **Physical Android devices, iOS, macOS, Linux desktop installers**: not built or tested. The Android check was one emulator (x86_64).
* **Network namespace isolation on Linux** was not exercised in this audit (the container did not permit it); the two tests that need it were skipped. Earlier runs by the author on a host that allows it are documented in `docs/STATUS.md`.
* **Malware scanning, DLP, OCR, accessibility with a screen reader, load/soak testing, GB-scale files, TLS and at-rest encryption** (deployment concerns) were not tested.
* `scripts/check-contracts.mjs` (API contract sync) was not run on Windows, where it cannot spawn its tool; no API schema was changed in this release work.
* **Legal**: GPL and LGPL obligations were inventoried (`THIRD_PARTY_NOTICES.md`) but not reviewed by a lawyer. Points to confirm: shipping FFmpeg (GPL build), Pandoc and Poppler in the installer with the source offer; py7zr (LGPL) inside a PyInstaller bundle.
* The bundled engines are pinned by SHA-256 (`scripts/engines.lock.json`) but their upstream builds were not independently rebuilt or audited.

## Before publishing: owner actions

1. **Back up** `%USERPROFILE%\.ezconverter-signing\` (keystore and `signing.env`). If the key is lost, installed APKs can never be updated; if it leaks, anyone can publish updates that Android accepts.
2. **Enable private vulnerability reporting** on the GitHub repository (Settings, Code security) so the link in `SECURITY.md` works.
3. Decide whether to rewrite git history to drop `zips/EZConverter.zip` (it contains only project files and `.env.example`, no secrets).
4. Upload the three files with `SHA256SUMS.txt` to a GitHub release marked as a **pre-release**, and paste the APK certificate fingerprint into the release notes.
5. Tell Windows users about the SmartScreen prompt (the README does), or buy a code-signing certificate.
