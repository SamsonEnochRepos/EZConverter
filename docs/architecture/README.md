# Architecture

## Layers and boundaries

```
 clients            apps/web   apps/desktop (Electron)   apps/mobile (Expo)
                      └────────────┬─────────────────────────┘
 shared TS             packages/ui · shared-domain (router, batch controller) · api-client · contracts (generated)
                                   │ HTTP/JSON, versioned /v1, one error envelope
 API                   backend/ezconverter/api            thin FastAPI layer: auth, rate limits, streaming upload, signed downloads
 orchestration         orchestration/{manager,store,naming}   durable queue (SQLite), bounded concurrency, cancel/retry/recovery, batches, retention
 domain (pure)         domain · detection · registry · planner · validation     no FastAPI/React/Electron/storage imports
 execution             workers/{launcher,entry,pipeline} → sandbox.py → engines/*  one sandboxed process per attempt
 engines               images (Pillow) · media (FFmpeg) · documents (LibreOffice, Pandoc, Poppler/pypdf) · archives · structured
```

The domain layer is independent of every framework: `detection`, `registry`, `planner` and `validation` import only the domain model and each
other. The API and the Electron/React/Expo apps are replaceable shells around it.

## Core contracts

* **Format** – canonical lowercase id (`png`, `docx`, `tgz`…) with family, MIME, extensions (`domain/formats.py`).
* **Capability** – `source → target`, operation (`convert`, `extract_audio`, `rasterize`, `repack`, `transform`), fidelity class, validated option
  schema, environments (local/remote), required tools, warnings, license, resource hints, `composable` flag (`domain/capability.py`).
* **EngineAdapter** – `capabilities()`, `availability()`, `validate_options()`, `assess()` (input-specific fidelity + warnings, may reject),
  `execute()`, `extra_checks()` (`engines/base.py`). Adapters build explicit argv lists from validated values; no user string reaches a shell.
* **Registry** – loads each engine independently (an import/probe failure disables only that engine). Active = dependency present, allowed in
  this runtime location, and listed in the *verified* manifest. Inactive capabilities stay listed with a reason.
* **Planner** – deterministic. Detects nothing itself (detection is upstream); finds a direct route, otherwise a route of ≤3 steps in which
  *every* step is explicitly `composable`. Prefers fewer steps, then better worst-step fidelity, then lexical order. Reports the worst step's
  fidelity and merged warnings, and rejects with a reason (known gaps such as raster→SVG or PDF→DOCX have specific explanations).
* **Job state machine** – `CREATED → VALIDATING → QUEUED → RUNNING → VERIFYING → SUCCEEDED`, plus `FAILED`/`CANCELLED`; the only re-entries are
  `RUNNING/VERIFYING → QUEUED` (bounded automatic retry after a lost worker) and `FAILED → QUEUED` (explicit retry of a retryable failure).
  Enforced in one place (`domain/states.py`) and recorded in `job_events`.

## Execution path of one job

1. Upload streams to a server-generated path (size and quota checked while streaming) → a *sandboxed* inspect worker identifies the file.
2. `plan` validates options and returns fidelity, warnings, estimates. `create` re-plans, persists `QUEUED`.
3. The dispatcher claims jobs atomically (`UPDATE … WHERE state='QUEUED'`), within slot and memory budgets, and runs one worker process per attempt.
4. The worker runs each plan step in its own scratch directory, **verifies each step's output**, and only then proceeds. The job enters `VERIFYING`
   only for the final step's checks.
5. On success the output is moved into artifact storage and the job becomes `SUCCEEDED` in one transaction with its provenance record
   (input/output SHA-256, steps, options, tool versions, duration). Otherwise the attempt directory is deleted.

## Hybrid local / remote

A *runtime* is one converter endpoint with `location = local|remote`. A desktop app owns a local sidecar runtime; a phone has only a remote one.
Policies: `LOCAL_ONLY`, `REMOTE_ALLOWED`, `AUTO`.

* **Server rule:** a remote runtime rejects `LOCAL_ONLY` and `AUTO` (409) *before reading the request body*; `AUTO` must be resolved by the client.
* **Client router** (`shared-domain/router.ts`): `LOCAL_ONLY` uses only local runtimes and never falls back; `AUTO` prefers local and needs standing
  consent for remote; `REMOTE_ALLOWED` is consent in itself but still prefers a capable local runtime. Failures say what was *not* uploaded.
* **Intake:** files are first read/identified on a local runtime. A remote runtime is used for intake only with consent (and never under `LOCAL_ONLY`).
* If a conversion's runtime differs from the intake runtime, the file is uploaded to it only when the batch starts.

## Decisions (ADR summary)

| # | Decision | Why / trade-off |
|---|---|---|
| 1 | Python backend; engines as external processes | Best ecosystem for conversion tools; process isolation is the security boundary. Cost: tools must be installed (documented, auto-detected). |
| 2 | One sandboxed worker *process* per job attempt | Uniform containment for Pillow/pypdf/YAML as well as FFmpeg/LibreOffice; kill-by-process-group cancellation. Cost: ~0.3 s start-up. |
| 3 | Capabilities enumerated per format pair, not "everything via an intermediate" | Prevents unsound routes; multi-step only through explicitly `composable` steps. |
| 4 | "Verified" manifest gates advertising | Satisfies "never advertise what isn't proven"; regenerated by the conformance run. |
| 5 | Same-format `TRANSFORM` capabilities | Resize/compress without changing format is a core need; the planner rejects it unless a non-default option makes it meaningful. |
| 6 | Raw-body uploads, not multipart | Allows refusing a forbidden policy before any byte is read; streams without buffering; works from browsers and React Native. |
| 7 | SQLite for job state + local filesystem for artifacts | Durable and simple for one node (the desktop sidecar and a single-node server). **Not** a multi-node queue: PostgreSQL + object storage is future work behind `Store`/`ArtifactStore`. |
| 8 | OpenAPI is the contract; TS types are generated and drift-checked | `node scripts/check-contracts.mjs` fails when the backend and committed types diverge. |
| 9 | Electron serves the UI from a privileged `ezc://app` scheme and talks to a loopback sidecar with a per-launch random token | No `file://`, no Node in the renderer, CORS restricted to the app origin. |
| 10 | Phones are remote-only | Conversion engines cannot realistically run in-process on iOS/Android; `LOCAL_ONLY` there refuses with an explanation. |

## Extending

Add a format: add it to `domain/formats.py` and teach `detection/sniffer.py` (+ a fixture in `tests/fixtures/generate.py`).
Add an engine: implement `EngineAdapter`, register it in `registry._builtin_adapters` (or load it as an operator plugin via `EZ_EXTRA_ENGINES`),
run the conformance suite (`pytest tests/conformance`) and `scripts/build_format_matrix.py`. The capability appears in clients only after it passes.
