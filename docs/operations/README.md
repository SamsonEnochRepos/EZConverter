# Operations

## Running a converter

```bash
export EZ_API_KEYS="$(python -c 'import secrets;print(secrets.token_urlsafe(32))'):alice"   # token:user[,token:user]
export EZ_LOCATION=remote EZ_DATA_DIR=/var/lib/ezconverter EZ_NETWORK_ISOLATION=require
python -m ezconverter serve --host 0.0.0.0 --port 8787          # put TLS (reverse proxy / LB) in front
```

A *local* runtime (desktop sidecar, single-user) uses `EZ_LOCATION=local` and `--sidecar` (loopback only, prints `EZCONVERTER_READY {json}`). Do **not** expose a
local runtime or `EZ_AUTH_MODE=none` on a network. All settings are documented in [`.env.example`](../../.env.example). The container draft is in
`deployment/containers/` (**never built**: no Docker daemon was available).

## Capacity and limits

* Concurrency = `EZ_MAX_CONCURRENCY` slots *and* a memory budget (estimated per job; default 75% of RAM, `EZ_MEMORY_BUDGET_MB`). A lone oversized job still runs.
* Per-job: wall-clock timeout, CPU seconds, address-space limit, max single-file output, plus capability-level input caps (e.g. 256 MB for Pillow conversions, 200 MB for LibreOffice).
* Per-user: upload size, storage quota, batch size, request rate.

## Retention, cleanup, deletion

* A sweeper (every `EZ_SWEEP_INTERVAL_S`) deletes uploads, artifacts, job rows and batch ZIPs older than `EZ_RETENTION_HOURS` (default 24 h). Files used by an active job are kept.
* Users can delete a file, job, batch or everything from the UI at once (`DELETE` endpoints); deletion removes bytes and rows.
* Per-attempt scratch directories are removed in a `finally` after every attempt, including failures and cancellations.
* Manual run: `JobManager.sweep()`; verified by `test_retention_sweep_removes_expired_data_but_keeps_active_work`.

## Recovery

State is durable in `EZ_DATA_DIR/ezconverter.db` (SQLite, WAL). On start, jobs left `RUNNING/VERIFYING` are re-queued if attempts remain (their partial work is discarded and rebuilt from the
original upload — conversions are **not** resumable mid-file), otherwise failed with `worker_lost`; jobs with a pending cancel become `CANCELLED`. Clients re-attach to running batches
(`BatchController.resume`). Verified by `test_restart_recovery_*` and the shared-domain restart test.

Backups: stop the service (or use `sqlite3 .backup`) and copy `EZ_DATA_DIR`; results are temporary by design, so most deployments back up nothing.

## Monitoring

See [deployment/observability/README.md](../../deployment/observability/README.md). Check `/v1/health` → `network_isolation` after every deploy.

## Updating engines / the capability set

1. Upgrade FFmpeg/LibreOffice/etc.; 2. `rm -rf tests/.conformance-results && pytest tests/conformance -n 4 && python scripts/build_format_matrix.py`; 3. ship the regenerated
`backend/ezconverter/registry/verified_capabilities.json`. A capability whose conformance case fails is not written to the manifest and therefore stops being advertised.

## Desktop packaging (Windows)

```powershell
python scripts/fetch_windows_engines.py      # FFmpeg, Pandoc, Poppler -> apps/desktop/engines (SHA-256 pinned in scripts/engines.lock.json)
python scripts/build_sidecar.py              # PyInstaller one-folder build of backend/ -> apps/desktop/sidecar
python scripts/smoke_packaged.py apps/desktop/sidecar/ezconverter-server.exe --engines apps/desktop/engines
pnpm --filter @ezconverter/web build; pnpm --filter @ezconverter/desktop build
cd apps/desktop; pnpm exec electron-builder --win     # release/EZConverter-Setup-<v>-win-x64.exe + portable zip
```

Run `python` from an environment with `pyinstaller` and `backend[sevenzip]` installed. The converter executable also serves as its own worker (`ezconverter-server.exe worker <request.json>`),
and the app points it at the bundled engines through `EZ_ENGINE_DIR`. LibreOffice is not bundled: it is found in `Program Files\LibreOffice` when installed. Code signing is not configured.
Release checklist: [docs/security/RELEASE_AUDIT.md](../security/RELEASE_AUDIT.md).
