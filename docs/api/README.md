# API (v1)

Machine-readable contract: [openapi.json](openapi.json), generated from the Pydantic models (`python -m ezconverter openapi`). Interactive docs at `/docs`.
TypeScript types are generated from it (`pnpm gen:contracts`) and drift is a test failure (`node scripts/check-contracts.mjs`).

## Conventions

* Auth: `Authorization: Bearer <token>` (except `/v1/health`). Cross-user and missing resources both return **404**.
* Errors: `{"error": {"code", "message", "details", "request_id"}}`; `message` is safe for end users. Codes: `invalid_request`, `unauthenticated`, `not_found`,
  `rate_limited`, `idempotency_conflict`, `invalid_state`, `local_only_violation`, `remote_consent_required`, `file_too_large`, `quota_exceeded`,
  `unsupported_input`, `malformed_input`, `unsafe_input`, `unsupported_conversion`, `invalid_options`, `engine_unavailable`, `conversion_failed`,
  `output_validation_failed`, `timeout`, `resource_limit`, `worker_lost`, `cancelled`, `internal_error`.
* Idempotency: send `Idempotency-Key` on `POST /v1/conversions` and `/v1/batches`; replays return the original resource (`Idempotent-Replay: true`), a different body with the same key is `409`.
* Execution policy: `X-EZ-Execution-Policy` on uploads; `execution_policy` in plan/convert/batch bodies. See [architecture](../architecture/README.md#hybrid-local--remote).

## Endpoints

| Method & path | Purpose |
|---|---|
| `GET /v1/health` | version, runtime location, network-isolation state, retention, limits |
| `GET /v1/capabilities` | formats, verified capabilities (options, fidelity, license, availability), engines. `?source=&target=&include_unavailable=` |
| `GET /v1/targets?source=` · `GET /v1/files/{id}/targets` | formats reachable (including multi-step) with fidelity |
| `POST /v1/files?filename=` | upload raw bytes, identify by content, returns detection details + `eligible` |
| `POST /v1/files/inspect?filename=` | identify without storing |
| `GET/DELETE /v1/files/{id}` | read / delete an upload |
| `POST /v1/conversions/plan` | validate options and describe the exact plan: steps, fidelity, warnings, estimate |
| `POST /v1/conversions` | create a job (202) |
| `GET /v1/jobs` · `GET /v1/jobs/{id}` | history · status (`progress` null = indeterminate) |
| `POST /v1/jobs/{id}/cancel` · `/retry` · `DELETE /v1/jobs/{id}` | cancel · explicit bounded retry · delete result |
| `GET /v1/jobs/{id}/result` | result metadata, verification report, provenance (409 unless `SUCCEEDED`) |
| `POST /v1/batches` · `GET /v1/batches/{id}` · `/cancel` · `/retry-failed` · `/result` · `DELETE` | batches |
| `GET /v1/batches/{id}/archive` · `POST /v1/batches/{id}/archive/link` | ZIP of successful outputs + `manifest.json` |
| `POST /v1/artifacts/{id}/link` · `GET /v1/artifacts/{id}/download` | 5-minute signed link · download (`attachment`) |

## Job lifecycle

`CREATED → VALIDATING → QUEUED → RUNNING → VERIFYING → SUCCEEDED`, or `FAILED` / `CANCELLED`. A failed job with `error.retryable` and attempts left can be retried (`can_retry`).
Cancellation of a *running* job is best effort: if the worker finishes first the job is `SUCCEEDED` (honest outcome); cancelling a finished job is `409`.
