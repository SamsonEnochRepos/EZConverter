"""FastAPI application: thin HTTP layer over the conversion domain."""

from __future__ import annotations

import hmac
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import Settings
from ..domain.capability import Location
from ..domain.errors import ErrorCode, EZError
from ..domain.formats import FORMATS
from ..domain.policy import ExecutionPolicy, enforce_location
from ..domain.states import JobState
from ..orchestration import JobManager, Store
from ..orchestration.naming import safe_filename
from ..registry import CapabilityRegistry, default_registry
from ..registry.conformance import load_verified
from ..storage.artifacts import LocalArtifactStore, new_id
from .schemas import (
    BatchOut,
    BatchRequest,
    BatchResultOut,
    CapabilitiesOut,
    ConversionRequest,
    DetectionOut,
    ErrorEnvelope,
    FileOut,
    Health,
    JobOut,
    JobResultOut,
    PlanOut,
    PlanRequest,
    SignedLink,
    TargetsOut,
)

log = logging.getLogger("ezconverter.api")
SIGNED_LINK_TTL = 300
ERR = {k: {"model": ErrorEnvelope} for k in (401, 404, 409, 413, 415, 422, 429, 503)}


@dataclass
class Runtime:
    settings: Settings
    registry: CapabilityRegistry
    store: Store
    storage: LocalArtifactStore
    manager: JobManager
    signing_key: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    _buckets: dict[str, tuple[float, int]] = field(default_factory=dict)
    verified_ids: set[str] | None = None

    def rate_check(self, owner: str) -> None:
        now = time.time()
        window = int(now // 60)
        w, n = self._buckets.get(owner, (window, 0))
        if w != window:
            w, n = window, 0
        n += 1
        self._buckets[owner] = (w, n)
        if n > self.settings.rate_limit_per_min:
            raise EZError(
                ErrorCode.RATE_LIMITED,
                "Too many requests. Please slow down.",
                {"retry_after_seconds": 60 - int(now % 60)},
            )


def build_runtime(settings: Settings, registry: CapabilityRegistry | None = None) -> Runtime:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    reg = registry or default_registry(settings.location)
    verified = load_verified(settings.conformance_manifest)
    if settings.advertise == "verified" and verified is not None:
        reg.restrict_to = verified
    store = Store(settings.data_dir / "ezconverter.db")
    storage = LocalArtifactStore(settings.data_dir)
    rt = Runtime(settings, reg, store, storage, JobManager(settings, reg, store, storage))
    rt.verified_ids = verified
    return rt


def isolation_state(settings: Settings) -> str:
    """Resolve the worker network-isolation mode; `require` fails closed, `auto` warns instead of degrading silently."""
    from ..sandbox import network_isolation_available

    if settings.network_isolation not in ("require", "auto", "off"):
        raise RuntimeError("EZ_NETWORK_ISOLATION must be one of: require, auto, off")
    if settings.network_isolation == "off":
        return "disabled"
    if network_isolation_available():
        return "active"
    if settings.network_isolation == "require":
        raise RuntimeError(
            "EZ_NETWORK_ISOLATION=require but this host cannot create network namespaces (unshare -n failed). "
            "Run the converter on a network with no outbound access instead, or set EZ_NETWORK_ISOLATION=auto."
        )
    log.warning(
        "worker network isolation is UNAVAILABLE on this host: converters run with network access. "
        "Place the service on an egress-denied network or fix user-namespace permissions."
    )
    return "unavailable"


def create_app(settings: Settings | None = None, registry: CapabilityRegistry | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    iso = isolation_state(settings)
    rt = build_runtime(settings, registry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        rt.manager.start()
        try:
            yield
        finally:
            rt.manager.stop()
            rt.store.close()

    app = FastAPI(
        title="EZConverter API",
        version=__version__,
        lifespan=lifespan,
        description="Universal file conversion. All routes are under /v1. Errors use a single envelope: "
        '`{"error": {"code", "message", "details", "request_id"}}`.',
    )
    app.state.rt = rt

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-EZ-Execution-Policy",
                "X-Request-ID",
            ],
            expose_headers=["Content-Disposition", "X-Request-ID"],
        )

    @app.middleware("http")
    async def common_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid[:64]
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = request.state.request_id
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/v1/") and "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def envelope(
        request: Request, code: str, message: str, status: int, details: dict | None = None
    ) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "details": details or {},
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
            status_code=status,
        )

    @app.exception_handler(EZError)
    async def _ez(request: Request, exc: EZError):  # noqa: ANN202
        headers = (
            {"Retry-After": str(exc.details["retry_after_seconds"])}
            if exc.code is ErrorCode.RATE_LIMITED
            else None
        )
        r = envelope(request, exc.code.value, exc.message, exc.http_status, exc.details)
        if headers:
            r.headers.update(headers)
        return r

    @app.exception_handler(RequestValidationError)
    async def _val(request: Request, exc: RequestValidationError):  # noqa: ANN202
        fields = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "problem": e["msg"]}
            for e in exc.errors()[:10]
        ]
        return envelope(request, "invalid_request", "The request is not valid.", 422, {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):  # noqa: ANN202
        code = {404: "not_found", 405: "invalid_request", 401: "unauthenticated"}.get(
            exc.status_code, "invalid_request"
        )
        return envelope(
            request, code, "Not found." if exc.status_code == 404 else str(exc.detail), exc.status_code
        )

    @app.exception_handler(Exception)
    async def _any(request: Request, exc: Exception):  # noqa: ANN202
        log.exception("unhandled error on %s", request.url.path)  # details stay in server logs only
        return envelope(request, "internal_error", "Something went wrong on our side.", 500)

    # ------------------------------------------------------------------ dependencies

    async def auth(request: Request) -> str:
        if settings.auth_mode == "none":
            owner = "local"
        else:
            header = request.headers.get("authorization", "")
            token = header[7:].strip() if header.lower().startswith("bearer ") else ""
            owner = None
            for (
                known,
                user,
            ) in settings.api_keys.items():  # compare against every key: no early exit timing signal
                if token and hmac.compare_digest(known.encode(), token.encode()):
                    owner = user
            if owner is None:
                raise EZError(ErrorCode.UNAUTHENTICATED, "A valid API token is required.")
        if request.method in ("POST", "DELETE"):
            rt.rate_check(owner)
        return owner

    def policy_of(value: str | None) -> ExecutionPolicy:
        """Upload policy: explicit on remote runtimes; defaults to LOCAL_ONLY on local ones."""
        if value is None:
            if rt.settings.location is Location.REMOTE:
                raise EZError(
                    ErrorCode.REMOTE_CONSENT_REQUIRED,
                    "This is a remote runtime: send X-EZ-Execution-Policy: REMOTE_ALLOWED to confirm the file may be uploaded.",
                )
            return ExecutionPolicy.LOCAL_ONLY
        try:
            return ExecutionPolicy(value)
        except ValueError as exc:
            raise EZError(ErrorCode.INVALID_REQUEST, "Unknown execution policy.") from exc

    # ------------------------------------------------------------------ views

    def file_view(f: dict[str, Any]) -> dict[str, Any]:
        det = f["detection"]
        reason = None
        if det.get("rejected_reason"):
            reason = det["rejected_reason"]
        elif det.get("damaged"):
            reason = "The file appears to be damaged. " + " ".join(det.get("notes", []))
        elif not f["format"]:
            reason = "The format was not recognised. " + " ".join(det.get("notes", []))
        return {
            "id": f["id"],
            "original_name": f["original_name"],
            "size": f["size"],
            "sha256": f["sha256"],
            "format": f["format"],
            "detection": det,
            "created_at": f["created_at"],
            "expires_at": f["expires_at"],
            "eligible": reason is None,
            "ineligible_reason": reason,
        }

    def job_view(j: dict[str, Any]) -> dict[str, Any]:
        err = j.get("error")
        return {
            **{
                k: j[k]
                for k in (
                    "id",
                    "batch_id",
                    "file_id",
                    "original_name",
                    "source_format",
                    "target_format",
                    "state",
                    "progress",
                    "phase",
                    "attempts",
                    "max_attempts",
                    "error",
                    "plan",
                    "artifact_id",
                    "created_at",
                    "updated_at",
                    "started_at",
                    "finished_at",
                    "expires_at",
                )
            },
            "warnings": j.get("warnings") or [],
            "cancel_requested": bool(j["cancel_requested"]),
            "can_retry": bool(
                j["state"] == "FAILED"
                and err
                and err.get("retryable")
                and j["attempts"] < j["max_attempts"]
                and j.get("plan")
            ),
        }

    def result_view(j: dict[str, Any]) -> dict[str, Any]:
        r = j["result"]
        return {
            "job_id": j["id"],
            "artifact_id": r["artifact_id"],
            "name": r["name"],
            "size": r["size"],
            "sha256": r["sha256"],
            "mime": r["mime"],
            "fidelity": r["fidelity"],
            "warnings": j.get("warnings") or [],
            "verification": r["verification"],
            "provenance": r["provenance"],
            "download_path": f"/v1/artifacts/{r['artifact_id']}/download",
        }

    def batch_view(b: dict[str, Any]) -> dict[str, Any]:
        return {**b, "jobs": [job_view(j) for j in b["jobs"]]}

    # ------------------------------------------------------------------ routes: meta

    @app.get("/v1/health", response_model=Health, tags=["meta"])
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "location": rt.settings.location.value,
            "api_version": "v1",
            "conformance": "verified" if rt.verified_ids is not None else "unverified",
            "network_isolation": iso,
            "retention_hours": rt.settings.retention_hours,
            "max_upload_mb": rt.settings.max_upload_mb,
            "max_batch_files": rt.settings.max_batch_files,
        }

    @app.get("/v1/capabilities", response_model=CapabilitiesOut, responses=ERR, tags=["capabilities"])
    async def capabilities(
        source: str | None = None,
        target: str | None = None,
        include_unavailable: bool = False,
        _owner: str = Depends(auth),
    ) -> dict[str, Any]:
        entries = rt.registry.all() if include_unavailable else rt.registry.active()
        out = []
        for e in entries:
            c = e.capability
            if (source and c.source != source) or (target and c.target != target):
                continue
            active = rt.registry.is_active(e)
            out.append(
                {
                    **c.to_dict(),
                    "composable": c.composable,
                    "available": active,
                    "verified": rt.verified_ids is not None and c.id in rt.verified_ids,
                    "unavailable_reason": None if active else rt.registry.inactive_reason(e),
                }
            )
        out.sort(key=lambda c: c["id"])
        return {
            "location": rt.settings.location.value,
            "formats": [
                {
                    "id": f.id,
                    "name": f.name,
                    "family": f.family.value,
                    "mime": f.mime,
                    "extensions": list(f.extensions),
                }
                for f in FORMATS.values()
            ],
            "capabilities": out,
            "engines": rt.registry.engines(),
        }

    @app.get("/v1/targets", response_model=TargetsOut, responses=ERR, tags=["capabilities"])
    async def targets(source: str = Query(max_length=16), _owner: str = Depends(auth)) -> dict[str, Any]:
        if source not in FORMATS:
            raise EZError(ErrorCode.INVALID_REQUEST, "Unknown source format.")
        return {
            "source": source,
            "targets": rt.manager.planner.reachable_targets(
                source,
                ExecutionPolicy.LOCAL_ONLY
                if rt.settings.location is Location.LOCAL
                else ExecutionPolicy.REMOTE_ALLOWED,
            ),
        }

    # ------------------------------------------------------------------ routes: files

    async def _receive(
        request: Request, filename: str, dest: Path, policy_header: str | None, owner: str
    ) -> tuple[int, str]:
        policy = policy_of(policy_header)
        enforce_location(policy, rt.settings.location)  # before a single body byte is read
        limit = rt.settings.max_upload_mb * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise EZError(
                ErrorCode.FILE_TOO_LARGE,
                f"Files can be at most {rt.settings.max_upload_mb} MB.",
                {"limit_mb": rt.settings.max_upload_mb},
            )
        quota_left = rt.settings.user_quota_mb * 1024 * 1024 - await run_in_threadpool(
            rt.store.owner_usage, owner
        )
        if quota_left <= 0:
            raise EZError(
                ErrorCode.QUOTA_EXCEEDED,
                "Your temporary storage is full. Delete some files or results first.",
            )
        h, size = sha256(), 0
        cap = min(limit, quota_left)
        try:
            with open(dest, "wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > cap:
                        raise EZError(
                            ErrorCode.FILE_TOO_LARGE if cap == limit else ErrorCode.QUOTA_EXCEEDED,
                            f"Files can be at most {rt.settings.max_upload_mb} MB."
                            if cap == limit
                            else "Your temporary storage quota would be exceeded.",
                        )
                    h.update(chunk)
                    fh.write(chunk)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        if size == 0:
            dest.unlink(missing_ok=True)
            raise EZError(ErrorCode.UNSUPPORTED_INPUT, "The file is empty.")
        return size, h.hexdigest()

    @app.post(
        "/v1/files",
        status_code=201,
        response_model=FileOut,
        responses=ERR,
        tags=["files"],
        summary="Upload a file (raw request body) and identify it by content",
    )
    async def upload(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        x_ez_execution_policy: str | None = Header(default=None),
        owner: str = Depends(auth),
    ) -> dict[str, Any]:
        enforce_location(
            policy_of(x_ez_execution_policy), rt.settings.location
        )  # nothing is created for a refused upload
        file_id = new_id()
        dest = rt.storage.upload_path(file_id)
        try:
            size, digest = await _receive(request, filename, dest, x_ez_execution_policy, owner)
            rec = await run_in_threadpool(rt.manager.add_file, owner, file_id, filename, size, digest)
        except BaseException:
            rt.storage.remove_upload(file_id)
            raise
        return file_view(rec)

    @app.post(
        "/v1/files/inspect",
        response_model=DetectionOut,
        responses=ERR,
        tags=["files"],
        summary="Identify a file by content without storing it",
    )
    async def inspect(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        x_ez_execution_policy: str | None = Header(default=None),
        owner: str = Depends(auth),
    ) -> dict[str, Any]:
        enforce_location(policy_of(x_ez_execution_policy), rt.settings.location)
        tmp = rt.storage.tmp_dir()
        try:
            await _receive(request, filename, tmp / "blob", x_ez_execution_policy, owner)
            return await run_in_threadpool(rt.manager.inspect_path, tmp / "blob", filename)
        finally:
            rt.storage.remove_tree(tmp)

    @app.get("/v1/files/{file_id}", response_model=FileOut, responses=ERR, tags=["files"])
    async def get_file(file_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        return file_view(rt.manager.get_file(owner, file_id))

    @app.delete("/v1/files/{file_id}", status_code=204, responses=ERR, tags=["files"])
    async def delete_file(file_id: str, owner: str = Depends(auth)) -> Response:
        await run_in_threadpool(rt.manager.delete_file, owner, file_id)
        return Response(status_code=204)

    @app.get(
        "/v1/files/{file_id}/targets",
        response_model=TargetsOut,
        responses=ERR,
        tags=["files"],
        summary="Formats this file can be converted to on this runtime",
    )
    async def file_targets(file_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        f = rt.manager.get_file(owner, file_id)
        if not f["format"] or f["detection"].get("damaged") or f["detection"].get("rejected_reason"):
            return {"source": f["format"], "targets": []}
        pol = (
            ExecutionPolicy.LOCAL_ONLY
            if rt.settings.location is Location.LOCAL
            else ExecutionPolicy.REMOTE_ALLOWED
        )
        return {"source": f["format"], "targets": rt.manager.planner.reachable_targets(f["format"], pol)}

    # ------------------------------------------------------------------ routes: conversions & jobs

    @app.post(
        "/v1/conversions/plan",
        response_model=PlanOut,
        responses=ERR,
        tags=["conversions"],
        summary="Validate a conversion and describe exactly what will happen",
    )
    async def plan(body: PlanRequest, owner: str = Depends(auth)) -> dict[str, Any]:
        _f, p = await run_in_threadpool(
            rt.manager.plan,
            owner,
            body.file_id,
            body.target,
            body.options,
            ExecutionPolicy(body.execution_policy),
        )
        return p.to_dict()

    @app.post("/v1/conversions", status_code=202, response_model=JobOut, responses=ERR, tags=["conversions"])
    async def create_conversion(
        body: ConversionRequest,
        response: Response,
        owner: str = Depends(auth),
        idempotency_key: str | None = Header(default=None, max_length=128),
    ) -> dict[str, Any]:
        def create() -> dict[str, Any]:
            return rt.manager.create_job(
                owner, body.file_id, body.target, body.options, ExecutionPolicy(body.execution_policy)
            )

        res, replay = await run_in_threadpool(
            rt.manager.idempotent, owner, idempotency_key, "conversion", body.model_dump(), create
        )
        job = rt.manager.get_job(owner, res) if replay else res
        if replay:
            response.headers["Idempotent-Replay"] = "true"
        return job_view(job)

    @app.get(
        "/v1/jobs", response_model=list[JobOut], responses=ERR, tags=["jobs"], summary="Recent jobs (history)"
    )
    async def list_jobs(
        limit: int = Query(50, ge=1, le=200), owner: str = Depends(auth)
    ) -> list[dict[str, Any]]:
        return [
            job_view(j)
            for j in rt.store.q(
                "SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC LIMIT ?", (owner, limit)
            )
        ]

    @app.get("/v1/jobs/{job_id}", response_model=JobOut, responses=ERR, tags=["jobs"])
    async def get_job(job_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        return job_view(rt.manager.get_job(owner, job_id))

    @app.post("/v1/jobs/{job_id}/cancel", response_model=JobOut, responses=ERR, tags=["jobs"])
    async def cancel_job(job_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        return job_view(await run_in_threadpool(rt.manager.cancel_job, owner, job_id))

    @app.post("/v1/jobs/{job_id}/retry", response_model=JobOut, responses=ERR, tags=["jobs"])
    async def retry_job(job_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        return job_view(await run_in_threadpool(rt.manager.retry_job, owner, job_id))

    @app.delete(
        "/v1/jobs/{job_id}",
        status_code=204,
        responses=ERR,
        tags=["jobs"],
        summary="Delete a finished job and its result",
    )
    async def delete_job(job_id: str, owner: str = Depends(auth)) -> Response:
        await run_in_threadpool(rt.manager.delete_job, owner, job_id)
        return Response(status_code=204)

    @app.get("/v1/jobs/{job_id}/result", response_model=JobResultOut, responses=ERR, tags=["jobs"])
    async def job_result(job_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        j = rt.manager.get_job(owner, job_id)
        if j["state"] != JobState.SUCCEEDED.value:
            raise EZError(
                ErrorCode.INVALID_STATE,
                f"The job has no result because it is {j['state']}.",
                {"state": j["state"]},
            )
        return result_view(j)

    # ------------------------------------------------------------------ routes: batches

    @app.post("/v1/batches", status_code=202, response_model=BatchOut, responses=ERR, tags=["batches"])
    async def create_batch(
        body: BatchRequest,
        response: Response,
        owner: str = Depends(auth),
        idempotency_key: str | None = Header(default=None, max_length=128),
    ) -> dict[str, Any]:
        def create() -> dict[str, Any]:
            return rt.manager.create_batch(
                owner, [i.model_dump() for i in body.items], ExecutionPolicy(body.execution_policy)
            )

        res, replay = await run_in_threadpool(
            rt.manager.idempotent, owner, idempotency_key, "batch", body.model_dump(), create
        )
        if replay:
            response.headers["Idempotent-Replay"] = "true"
            res = rt.manager.batch_summary(owner, res)
        return batch_view(res)

    @app.get("/v1/batches/{batch_id}", response_model=BatchOut, responses=ERR, tags=["batches"])
    async def get_batch(batch_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        return batch_view(rt.manager.batch_summary(owner, batch_id))

    @app.post("/v1/batches/{batch_id}/cancel", response_model=BatchOut, responses=ERR, tags=["batches"])
    async def cancel_batch(batch_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        return batch_view(await run_in_threadpool(rt.manager.cancel_batch, owner, batch_id))

    @app.post("/v1/batches/{batch_id}/retry-failed", response_model=BatchOut, responses=ERR, tags=["batches"])
    async def retry_batch(batch_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        return batch_view(await run_in_threadpool(rt.manager.retry_batch_failures, owner, batch_id))

    @app.get("/v1/batches/{batch_id}/result", response_model=BatchResultOut, responses=ERR, tags=["batches"])
    async def batch_result(batch_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        b = rt.manager.batch_summary(owner, batch_id)
        jobs = b["jobs"]
        return {
            "batch_id": batch_id,
            "succeeded": [result_view(j) for j in jobs if j["state"] == "SUCCEEDED"],
            "failed": [job_view(j) for j in jobs if j["state"] == "FAILED"],
            "cancelled": [job_view(j) for j in jobs if j["state"] == "CANCELLED"],
            "archive_available": b["archive_available"],
            "archive_path": f"/v1/batches/{batch_id}/archive" if b["archive_available"] else None,
        }

    @app.get(
        "/v1/batches/{batch_id}/archive",
        responses={**ERR, 200: {"content": {"application/zip": {}}}},
        tags=["batches"],
        summary="ZIP of all successful outputs plus manifest.json",
    )
    async def batch_archive(batch_id: str, owner: str = Depends(auth)) -> FileResponse:
        art = await run_in_threadpool(rt.manager.build_batch_archive, owner, batch_id)
        return _file_response(art)

    @app.post(
        "/v1/batches/{batch_id}/archive/link",
        response_model=SignedLink,
        responses=ERR,
        tags=["batches"],
        summary="Short-lived URL for downloading the batch ZIP without an Authorization header",
    )
    async def batch_archive_link(batch_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        art = await run_in_threadpool(rt.manager.build_batch_archive, owner, batch_id)
        exp = int(time.time()) + SIGNED_LINK_TTL
        return {
            "url": f"/v1/artifacts/{art['id']}/download?exp={exp}&sig={_sign(art['id'], owner, exp)}",
            "expires_at": exp,
        }

    @app.delete("/v1/batches/{batch_id}", status_code=204, responses=ERR, tags=["batches"])
    async def delete_batch(batch_id: str, owner: str = Depends(auth)) -> Response:
        await run_in_threadpool(rt.manager.delete_batch, owner, batch_id)
        return Response(status_code=204)

    # ------------------------------------------------------------------ routes: artifacts

    def _sign(artifact_id: str, owner: str, exp: int) -> str:
        return hmac.new(rt.signing_key, f"{artifact_id}|{owner}|{exp}".encode(), sha256).hexdigest()

    def _file_response(art: dict[str, Any]) -> FileResponse:
        name = safe_filename(art["name"])
        ascii_name = (name.encode("ascii", "ignore").decode() or "download").replace('"', "")
        headers = {
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, no-store",
        }
        return FileResponse(
            art["path"],
            media_type="application/octet-stream"
            if art["mime"] in ("text/html", "image/svg+xml")
            else art["mime"],
            headers=headers,
        )

    @app.post(
        "/v1/artifacts/{artifact_id}/link",
        response_model=SignedLink,
        responses=ERR,
        tags=["artifacts"],
        summary="Short-lived URL for browser downloads that cannot send an Authorization header",
    )
    async def artifact_link(artifact_id: str, owner: str = Depends(auth)) -> dict[str, Any]:
        rt.manager.get_artifact(owner, artifact_id)
        exp = int(time.time()) + SIGNED_LINK_TTL
        return {
            "url": f"/v1/artifacts/{artifact_id}/download?exp={exp}&sig={_sign(artifact_id, owner, exp)}",
            "expires_at": exp,
        }

    @app.get(
        "/v1/artifacts/{artifact_id}/download",
        responses={**ERR, 200: {"content": {"application/octet-stream": {}}}},
        tags=["artifacts"],
    )
    async def download(
        request: Request, artifact_id: str, exp: int | None = None, sig: str | None = None
    ) -> FileResponse:
        if sig and exp:
            art = rt.store.get_artifact(artifact_id)
            if (
                not art
                or exp < time.time()
                or not hmac.compare_digest(sig, _sign(artifact_id, art["owner"], exp))
            ):
                raise EZError(ErrorCode.NOT_FOUND, "Result not found or the link expired.")
            owner = art["owner"]
        else:
            owner = await auth(request)
        return _file_response(rt.manager.get_artifact(owner, artifact_id))

    return app
