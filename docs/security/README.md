# Security model

Everything uploaded is treated as hostile. This page states the threat model, the controls in the code, and — equally important — what is **not** covered.
Each control names the test that exercises it (`tests/security`, `tests/integration/test_fault_handling.py`, app e2e suites).

## Assets and actors

* Assets: users' files and results, access tokens, the host running the converter, other users' data.
* Actors: a malicious file author (parser/engine exploits, bombs, path tricks), a malicious or curious authenticated user (cross-tenant access,
  abuse), a malicious web page or local process targeting the desktop sidecar, a network attacker against a remote runtime.

## Controls

| Threat | Control | Evidence |
|---|---|---|
| Parser/engine exploit in a converter | Every engine and every parser of untrusted bytes (including format detection and output verification) runs in a **separate sandboxed process**: scrubbed env (no inherited secrets), `prlimit` CPU/address-space/file-size limits, empty network namespace (`unshare -n`), own process group killed on timeout/cancel, private scratch dir deleted afterwards | `test_sandbox.py`, `test_fault_handling.py` (SIGSEGV contained, OOM → `resource_limit`, timeout, network probe blocked, env names asserted) |
| Windows worker confinement | Workers run in a Job Object (`winjob.py`): committed-memory and CPU-time caps, at most 16 processes, no desktop/clipboard/system-parameter access, whole-tree termination on timeout/cancel and when the job closes; the process is created suspended and resumed only after it is inside the job; failure to confine aborts the worker (fail closed); packaged builds hand workers only the engine and Windows system directories on `PATH` | `tests/security/test_windows_job.py` (memory, CPU, process-count, tree-kill, scrubbed env; Windows only) |
| Document-borne network access without a network namespace | LibreOffice is configured with a manual proxy on a closed local port for HTTP, HTTPS and FTP and an empty bypass list, so linked images/stylesheets/OLE links fail instead of connecting (needed on Windows and in default Docker, where `unshare -n` is unavailable) | `test_libreoffice_does_not_fetch_remote_resources_or_run_macros` (19 outbound requests without the setting, 0 with it) |
| Silent loss of the sandbox | `EZ_NETWORK_ISOLATION=require` refuses to start without namespaces; `auto` logs a warning; the real state is in `/v1/health` | `test_network_isolation_modes_fail_closed_or_warn` |
| Command injection | argv lists only; `run_process` rejects strings; option values are type/range/enum-validated before use; filenames never reach argv (input is copied to a fixed name) | `test_arguments_are_never_shell_interpreted`, `test_option_values_cannot_inject_commands_or_arguments`, hostile-filename matrix |
| Format spoofing | content sniffing; extension/MIME/client hints ignored (mismatch is surfaced to the user) | `test_extension_is_never_trusted`, `test_spoofed_content_type_and_extension_are_ignored` |
| Path traversal / archive attacks | archives are **streamed** member-by-member into the target (nothing is extracted to a path derived from member names); names are normalised/rejected (`..`, absolute, drive letters, over-long); symlinks/hardlinks/devices skipped; member-count, total-size and expansion-ratio limits; encrypted archives refused; nested archives stay opaque | `tests/security/test_archives.py` |
| Decompression / expansion bombs | gzip and archive ratio+size caps; image pixel cap (decoder never runs on over-limit images); YAML alias bomb budget; XML via `defusedxml` (DTD/entity/XXE forbidden); JSON/YAML depth guards; output size caps while writing | `test_yaml_alias_bomb_*`, `test_xml_entity_bomb_and_xxe_*`, `test_image_bomb_*` |
| Document-borne active content / data exfiltration | LibreOffice: per-job profile, macros disabled, no network; Pandoc: `--sandbox` where the writer permits, trusted Lua filter strips images/raw content for docx/odt so `![](/path)` cannot embed local files | `test_pandoc_does_not_embed_local_files_*`, `test_libreoffice_does_not_fetch_remote_resources_*` |
| Cross-user access | every file/job/batch/artifact lookup is owner-scoped and returns the same 404 for "missing" and "not yours"; signed download links are HMAC-bound to one artifact and expire in 5 min | `test_users_cannot_touch_each_others_data` |
| Credential guessing / timing | bearer tokens compared in constant time against all keys; tokens only from env | `test_every_route_but_health_requires_a_valid_token` |
| Abuse / exhaustion | upload size (checked on `Content-Length` and while streaming), per-user quota, per-user rate limit, batch size cap, bounded concurrency + memory budget, job timeout, bounded retries | `test_oversized_upload_*`, `test_storage_quota_and_rate_limit`, concurrency test |
| Policy bypass (privacy) | `LOCAL_ONLY` never uploads: refused client-side, and server-side before the body is read; nothing is created on disk for a refused upload; consent is required for remote use | `test_remote_runtime_never_accepts_*`, `shared-domain/test/*policy*`, mobile e2e "uploads nothing" |
| Stored data exposure | server-generated ids only; names never used as paths; retention sweeper + explicit delete endpoints; downloads served `attachment` + `nosniff` + `CSP: sandbox` (HTML/SVG outputs forced to octet-stream) | hostile-name tests, `test_retention_sweep_*` |
| Information leakage in errors/logs | user-facing messages are fixed strings; engine stderr is trimmed and path-scrubbed; unexpected exceptions return a generic envelope with a request id; tokens/file contents are not logged | envelope tests |
| Electron renderer compromise | `contextIsolation`, `sandbox`, no Node, no webview, DevTools off when packaged; renderer served from `ezc://app` with a strict CSP; navigation/popups/permissions denied; only two IPC channels, sender-origin checked and payload-validated; downloads only from the sidecar/https; sidecar bound to loopback with a random per-launch token and CORS limited to `ezc://app`; fuses configured in `electron-builder.yml` | `apps/desktop/test` (21) and real-Electron e2e (7) |
| Mobile credential handling | tokens in Keychain/Keystore (`WHEN_UNLOCKED_THIS_DEVICE_ONLY`), never in plain storage; `http://` addresses warned about | `apps/mobile/test/platform.test.ts` |
| Vulnerable dependencies | `pip-audit` clean; `pnpm audit --prod`: 4 findings all in Expo *build-time* tooling (see STATUS) | `scripts/verify.sh` |

## Not covered / residual risk (read this)

* **The sandbox is process-level, not a VM.** A kernel or container-runtime escape from a compromised engine is out of scope; for a public service, run workers in a
  container/gVisor/Firecracker with a read-only root and egress denied (`deployment/containers/docker-compose.yml`, unverified draft).
* **Network namespaces may be unavailable** (default Docker seccomp, some CI). Use `require` where it must hold, or enforce egress denial at the network layer.
* **Memory limit is `RLIMIT_AS`** (address space). LibreOffice needs generous limits, so it is not a tight cap; there are no cgroup CPU/memory quotas.
* **TLS and at-rest encryption are deployment concerns**: the service speaks HTTP; terminate TLS in front. Artifacts are stored unencrypted on the data volume (use disk/volume encryption).
* **No malware scanning** of uploads or outputs; no OCR/DLP. Converted documents can still contain whatever the source contained.
* **Authentication is static API tokens.** No user accounts, SSO, token rotation or per-token scopes; the rate limiter is in-memory per process.
* **CSV formula injection** is opt-in mitigation (`neutralize_formulas`), because altering cell text by default would corrupt data.
* **SVG as input** is rendered by LibreOffice; DTD/entity handling inside LibreOffice's importer is not independently verified (network is isolated, local file disclosure via entities is not specifically tested).
* **Windows sandboxing** has resource limits and tree-kill (Job Object) but **no network isolation**: a Windows process tree can open sockets. A converter engine that is exploited could therefore reach the network and the user's files. **macOS** has neither (no `prlimit`/`unshare`, no equivalent implemented).
* **Android release hardening**: cleartext HTTP is blocked except to loopback (`network_security_config.xml`), `allowBackup` is off, and the template's overlay/storage/vibrate permissions are removed. The APK is signed with a project key that must be kept offline.
* Electron packaged-build hardening: the fuses and ASAR integrity of the built Windows app were read back from the binary (RunAsNode, NODE_OPTIONS and `--inspect` off; ASAR integrity and only-load-from-ASAR on). The installer is **not code-signed**; macOS notarisation and auto-update do not exist.
