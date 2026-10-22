# Security policy

EZConverter parses files from untrusted sources, so security reports are welcome and taken seriously.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private reporting: open the repository's **Security** tab and choose **Report a vulnerability**
(https://github.com/SamsonEnochRepos/EZConverter/security/advisories/new). Include the affected version and platform,
steps to reproduce (a sample file helps), and the impact you see.

You can expect an acknowledgement within a few days. This is a volunteer-maintained project, so fix times depend on severity and
complexity; reporters are credited in the release notes unless they prefer otherwise.

## Supported versions

Only the latest release receives security fixes while the project is below 1.0.

## Scope

In scope: the converter backend and API, the sandbox, the desktop (Electron) shell, the Android app, and the web app.
Out of scope: vulnerabilities in third-party engines (FFmpeg, Pandoc, Poppler, LibreOffice) that are not made reachable or worse by
EZConverter. Report those upstream. Findings that require a deployment the documentation tells you not to run (for example
`EZ_AUTH_MODE=none` on a public network) are also out of scope.

## What the project does and does not protect against

The threat model, the controls and the test that covers each one are in [docs/security/README.md](docs/security/README.md).
The pre-release audit, with exactly what was and was not tested, is in [docs/security/RELEASE_AUDIT.md](docs/security/RELEASE_AUDIT.md).
Platform differences matter, in particular:

* **Linux** (self-hosted server, Docker): workers get CPU, memory and file-size limits and, where the host allows user namespaces,
  an empty network namespace.
* **Windows** (desktop app): workers run inside a Job Object (memory, CPU-time and process-count limits, no desktop or clipboard access,
  whole-tree kill) but **without network isolation**. LibreOffice is additionally forced to a dead proxy so documents cannot make it fetch
  remote content.
* **Android and web**: these are clients. Files are converted by the converter you point them at; use HTTPS.

## Verifying downloads

Release assets are published with SHA-256 checksums (`SHA256SUMS.txt`). The Android APK is signed with the project's release key;
its certificate SHA-256 fingerprint is printed in the README and the release notes. The Windows installer is **not code-signed**
(a certificate costs money), so Windows SmartScreen will warn on first run; verify the checksum instead.
