"""Job orchestration: planning, durable queue, bounded concurrency, cancellation, recovery, batches.

The queue is the SQLite `jobs` table, so state survives restarts. A dispatcher thread claims
QUEUED jobs atomically and runs each in a sandboxed worker process. A job is only marked
SUCCEEDED after the worker reports that the output passed verification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Settings
from ..domain.errors import ErrorCode, EZError
from ..domain.formats import FORMATS
from ..domain.policy import ExecutionPolicy, enforce_location
from ..domain.states import JobState
from ..engines.base import InputInfo
from ..planner import ConversionPlan, Planner
from ..registry import CapabilityRegistry
from ..sandbox import Limits
from ..storage.artifacts import LocalArtifactStore, new_id
from ..workers.launcher import run_worker
from .naming import dedupe_names, output_filename, safe_filename
from .store import Store

log = logging.getLogger("ezconverter.manager")
NON_COMPRESSIBLE = {
    "jpeg",
    "png",
    "webp",
    "avif",
    "gif",
    "mp3",
    "aac",
    "m4a",
    "ogg",
    "opus",
    "flac",
    "mp4",
    "mkv",
    "mov",
    "webm",
    "zip",
    "tgz",
    "gz",
    "7z",
    "docx",
    "xlsx",
    "pptx",
    "odt",
    "ods",
    "odp",
}


def _mem_budget_mb() -> int:
    env = os.environ.get("EZ_MEMORY_BUDGET_MB")
    if env and env.isdigit():
        return int(env)
    if sys.platform == "win32":
        return _windows_mem_budget_mb()
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1048576 * 0.75)
    except (ValueError, OSError):
        return 4096


def _windows_mem_budget_mb() -> int:
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return 4096
    return int(status.ullTotalPhys / 1048576 * 0.75)


class JobManager:
    def __init__(
        self, settings: Settings, registry: CapabilityRegistry, store: Store, storage: LocalArtifactStore
    ):
        self.s = settings
        self.registry = registry
        self.planner = Planner(registry)
        self.store = store
        self.storage = storage
        self._pool = ThreadPoolExecutor(max_workers=settings.max_concurrency, thread_name_prefix="ez-job")
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._running: dict[str, int] = {}  # job id -> estimated memory MB
        self._cancel: dict[str, threading.Event] = {}
        self._cancel_lock = threading.Lock()  # guards _cancel: written by runner threads, read by API/stop
        self._threads: list[threading.Thread] = []
        self._idem_lock = threading.Lock()
        self.mem_budget_mb = _mem_budget_mb()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.recover()
        for target, name in ((self._dispatch_loop, "ez-dispatch"), (self._sweep_loop, "ez-sweep")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        # Kill the workers, but this is a shutdown, not a user cancellation: _finish_error re-queues the
        # interrupted jobs (see there) instead of recording them as CANCELLED.
        with self._cancel_lock:
            events = list(self._cancel.values())
        for ev in events:
            ev.set()
        with self._cv:
            self._cv.notify_all()
        for t in self._threads:
            t.join(timeout=5)
        self._pool.shutdown(wait=True, cancel_futures=True)

    def recover(self) -> None:
        """After a restart, jobs left RUNNING/VERIFYING lost their worker. Re-queue (bounded) or fail them."""
        for j in self.store.jobs_in_states(["RUNNING", "VERIFYING"]):
            self.storage.remove_job(j["id"])
            if j["attempts"] < j["max_attempts"] and not j["cancel_requested"]:
                self.store.transition(j["id"], JobState.QUEUED, note="re-queued after restart")
            elif j["cancel_requested"]:
                self.store.transition(j["id"], JobState.CANCELLED, note="cancelled during restart")
            else:
                self.store.transition(
                    j["id"],
                    JobState.FAILED,
                    note="worker lost; attempts exhausted",
                    error=_err(
                        ErrorCode.WORKER_LOST,
                        "The service restarted while converting and the retry limit was reached.",
                    ),
                )
        for j in self.store.jobs_in_states(["CREATED", "VALIDATING"]):
            self.store.transition(
                j["id"],
                JobState.FAILED,
                note="interrupted before queueing",
                error=_err(ErrorCode.WORKER_LOST, "The service restarted before this job was queued."),
            )

    # ------------------------------------------------------------------ files

    def limits(self) -> Limits:
        s = self.s
        return Limits(
            wall_seconds=s.job_timeout_s,
            cpu_seconds=s.worker_cpu_s,
            memory_mb=s.worker_memory_mb,
            output_file_mb=s.worker_output_mb,
            isolate_network=s.isolate_network,
        )

    def inspect_path(self, path: Path, filename: str) -> dict[str, Any]:
        """Identify a file by content, inside a sandboxed worker (parsers never run in the API process)."""
        work = self.storage.tmp_dir()
        try:
            res = run_worker(
                {"op": "inspect", "path": str(path), "filename": filename, "work_dir": str(work)},
                work,
                Limits(
                    wall_seconds=60, cpu_seconds=60, memory_mb=3072, isolate_network=self.s.isolate_network
                ),
            )
        finally:
            self.storage.remove_tree(work)
        if not res.get("ok"):
            raise EZError(ErrorCode.INTERNAL, "The file could not be inspected.")
        return res["detection"]

    def add_file(
        self, owner: str, file_id: str, original_name: str, size: int, sha256: str
    ) -> dict[str, Any]:
        path = self.storage.upload_path(file_id)
        det = self.inspect_path(path, original_name)
        now = time.time()
        rec = {
            "id": file_id,
            "owner": owner,
            "original_name": safe_filename(original_name),
            "path": str(path),
            "size": size,
            "sha256": sha256,
            "format": det["format"],
            "detection": det,
            "created_at": now,
            "expires_at": now + self.s.retention_hours * 3600,
        }
        self.store.add_file(rec)
        return rec

    def get_file(self, owner: str, file_id: str) -> dict[str, Any]:
        f = self.store.get_file(file_id)
        if not f or f["owner"] != owner:  # same response for missing and foreign: no existence oracle
            raise EZError(ErrorCode.NOT_FOUND, "File not found.")
        return f

    def delete_file(self, owner: str, file_id: str) -> None:
        f = self.get_file(owner, file_id)
        active = [
            j
            for j in self.store.q(
                "SELECT id FROM jobs WHERE file_id=? AND state IN ('CREATED','VALIDATING','QUEUED','RUNNING','VERIFYING')",
                (file_id,),
            )
        ]
        if active:
            raise EZError(ErrorCode.INVALID_STATE, "This file is being converted. Cancel the job first.")
        self.storage.remove_upload(f["id"])
        self.store.delete_file(f["id"])

    # ------------------------------------------------------------------ planning

    def _input_info(self, f: dict[str, Any]) -> InputInfo:
        det = f["detection"]
        if det.get("rejected_reason"):
            raise EZError(
                ErrorCode.UNSAFE_INPUT if "safe" in det["rejected_reason"] else ErrorCode.UNSUPPORTED_INPUT,
                det["rejected_reason"],
            )
        if det.get("damaged"):
            raise EZError(
                ErrorCode.MALFORMED_INPUT,
                "This file appears to be damaged: " + " ".join(det.get("notes", [])),
            )
        if not f["format"]:
            raise EZError(
                ErrorCode.UNSUPPORTED_INPUT,
                "EZConverter could not recognise this file's format. " + " ".join(det.get("notes", [])),
            )
        return InputInfo(Path(f["path"]), f["original_name"], f["format"], f["size"], det.get("details", {}))

    def plan(
        self, owner: str, file_id: str, target: str, options: dict[str, Any] | None, policy: ExecutionPolicy
    ) -> tuple[dict[str, Any], ConversionPlan]:
        enforce_location(policy, self.s.location)
        f = self.get_file(owner, file_id)
        return f, self.planner.plan(self._input_info(f), target, options, policy)

    # ------------------------------------------------------------------ jobs

    def create_job(
        self,
        owner: str,
        file_id: str,
        target: str,
        options: dict[str, Any] | None,
        policy: ExecutionPolicy,
        batch_id: str | None = None,
        fail_soft: bool = False,
    ) -> dict[str, Any]:
        enforce_location(policy, self.s.location)
        f = self.get_file(owner, file_id)
        jid = new_id()
        now = time.time()
        base = {
            "id": jid,
            "owner": owner,
            "batch_id": batch_id,
            "file_id": file_id,
            "original_name": f["original_name"],
            "source_format": f["format"] or "unknown",
            "target_format": target,
            "options": options or {},
            "policy": policy.value,
            "max_attempts": self.s.max_attempts,
            "expires_at": now + self.s.retention_hours * 3600,
        }
        try:
            plan = self.planner.plan(self._input_info(f), target, options, policy)
        except EZError as exc:
            if not fail_soft:
                raise
            self.store.add_job({**base, "state": JobState.CREATED.value})
            self.store.transition(jid, JobState.VALIDATING, note="validating")
            self.store.transition(
                jid,
                JobState.FAILED,
                note="validation failed",
                error=_err(exc.code, exc.message, exc.details, retryable=False),
            )
            return self.store.get_job(jid)  # type: ignore[return-value]
        self.store.add_job(
            {
                **base,
                "state": JobState.CREATED.value,
                "plan": plan.to_dict(),
                "warnings": plan.warnings,
                "options": plan.options,
            }
        )
        self.store.transition(jid, JobState.VALIDATING, note="validating")
        self.store.transition(jid, JobState.QUEUED, note="queued")
        with self._cv:
            self._cv.notify_all()
        return self.store.get_job(jid)  # type: ignore[return-value]

    def get_job(self, owner: str, job_id: str) -> dict[str, Any]:
        j = self.store.get_job(job_id)
        if not j or j["owner"] != owner:
            raise EZError(ErrorCode.NOT_FOUND, "Job not found.")
        return j

    def cancel_job(self, owner: str, job_id: str) -> dict[str, Any]:
        j = self.get_job(owner, job_id)
        state = JobState(j["state"])
        if state.terminal:
            raise EZError(
                ErrorCode.INVALID_STATE, f"This job already finished ({state.value}) and cannot be cancelled."
            )
        if state in (JobState.CREATED, JobState.VALIDATING, JobState.QUEUED) and self.store.transition(
            job_id,
            JobState.CANCELLED,
            expect={JobState.CREATED, JobState.VALIDATING, JobState.QUEUED},
            note="cancelled before start",
            cancel_requested=1,
            error=_err(ErrorCode.CANCELLED, "Cancelled before it started.", retryable=False),
        ):
            return self.store.get_job(job_id)  # type: ignore[return-value]
        # running: flag it and kill the worker; the runner thread performs the transition
        self.store.update_job(job_id, cancel_requested=1)
        with self._cancel_lock:
            ev = self._cancel.get(job_id)
        if ev:
            ev.set()
        return self.store.get_job(job_id)  # type: ignore[return-value]

    def retry_job(self, owner: str, job_id: str) -> dict[str, Any]:
        j = self.get_job(owner, job_id)
        err = j.get("error") or {}
        if j["state"] != JobState.FAILED.value:
            raise EZError(ErrorCode.INVALID_STATE, "Only failed jobs can be retried.")
        if not err.get("retryable"):
            raise EZError(
                ErrorCode.INVALID_STATE,
                "This failure is caused by the input file, so retrying would fail again. Fix or replace the file.",
            )
        if j["attempts"] >= j["max_attempts"]:
            raise EZError(ErrorCode.INVALID_STATE, "The retry limit for this job has been reached.")
        if not j.get("plan"):
            raise EZError(ErrorCode.INVALID_STATE, "This job was never planned and cannot be retried.")
        if not self.store.get_file(j["file_id"]):
            raise EZError(
                ErrorCode.INVALID_STATE, "The source file has expired or was deleted. Upload it again."
            )
        self.store.transition(
            job_id,
            JobState.QUEUED,
            expect={JobState.FAILED},
            note="manual retry",
            error=None,
            cancel_requested=0,
            finished_at=None,
            progress=None,
            phase=None,
        )
        with self._cv:
            self._cv.notify_all()
        return self.store.get_job(job_id)  # type: ignore[return-value]

    def delete_job(self, owner: str, job_id: str) -> None:
        j = self.get_job(owner, job_id)
        if not JobState(j["state"]).terminal:
            raise EZError(ErrorCode.INVALID_STATE, "Cancel the job before deleting it.")
        self._purge_job(j)

    def _purge_job(self, j: dict[str, Any]) -> None:
        if j.get("artifact_id"):
            self.storage.remove_artifact(j["artifact_id"])
            self.store.delete_artifact(j["artifact_id"])
        self.storage.remove_job(j["id"])
        with self.store.tx() as db:
            db.execute("DELETE FROM job_events WHERE job_id=?", (j["id"],))
            db.execute("DELETE FROM jobs WHERE id=?", (j["id"],))

    # ------------------------------------------------------------------ batches

    def create_batch(
        self, owner: str, items: list[dict[str, Any]], policy_default: ExecutionPolicy
    ) -> dict[str, Any]:
        if not items:
            raise EZError(ErrorCode.INVALID_REQUEST, "A batch needs at least one item.")
        if len(items) > self.s.max_batch_files:
            raise EZError(
                ErrorCode.INVALID_REQUEST, f"A batch can contain at most {self.s.max_batch_files} files."
            )
        for it in items:  # fail fast on foreign / unknown file ids or refused policies: no half-created batch
            self.get_file(owner, it["file_id"])
            enforce_location(
                ExecutionPolicy(it.get("execution_policy") or policy_default.value), self.s.location
            )
        now = time.time()
        bid = new_id()
        self.store.add_batch(
            {"id": bid, "owner": owner, "created_at": now, "expires_at": now + self.s.retention_hours * 3600}
        )
        for it in items:
            self.create_job(
                owner,
                it["file_id"],
                it["target"],
                it.get("options"),
                ExecutionPolicy(it.get("execution_policy") or policy_default.value),
                batch_id=bid,
                fail_soft=True,
            )
        return self.batch_summary(owner, bid)

    def get_batch(self, owner: str, batch_id: str) -> dict[str, Any]:
        b = self.store.get_batch(batch_id)
        if not b or b["owner"] != owner:
            raise EZError(ErrorCode.NOT_FOUND, "Batch not found.")
        return b

    def batch_summary(self, owner: str, batch_id: str) -> dict[str, Any]:
        b = self.get_batch(owner, batch_id)
        jobs = self.store.batch_jobs(batch_id)
        counts: dict[str, int] = {}
        prog, indeterminate = 0.0, False
        for j in jobs:
            counts[j["state"]] = counts.get(j["state"], 0) + 1
            if JobState(j["state"]).terminal:
                prog += 1.0
            elif j["state"] in ("RUNNING", "VERIFYING"):
                if j["progress"] is None:
                    indeterminate = True
                else:
                    prog += j["progress"]
        done = all(JobState(j["state"]).terminal for j in jobs)
        return {
            "id": b["id"],
            "created_at": b["created_at"],
            "expires_at": b["expires_at"],
            "state": "COMPLETED"
            if done
            else ("RUNNING" if any(j["state"] in ("RUNNING", "VERIFYING") for j in jobs) else "QUEUED"),
            "counts": counts,
            "total": len(jobs),
            "progress": (prog / len(jobs)) if jobs else 1.0,
            "progress_indeterminate": indeterminate and not done,
            "jobs": jobs,
            "archive_available": done and counts.get("SUCCEEDED", 0) > 0,
        }

    def cancel_batch(self, owner: str, batch_id: str) -> dict[str, Any]:
        self.get_batch(owner, batch_id)
        for j in self.store.batch_jobs(batch_id):
            if not JobState(j["state"]).terminal:
                try:
                    self.cancel_job(owner, j["id"])
                except EZError:
                    pass
        return self.batch_summary(owner, batch_id)

    def retry_batch_failures(self, owner: str, batch_id: str) -> dict[str, Any]:
        self.get_batch(owner, batch_id)
        for j in self.store.batch_jobs(batch_id):
            if (
                j["state"] == "FAILED"
                and (j.get("error") or {}).get("retryable")
                and j["attempts"] < j["max_attempts"]
            ):
                try:
                    self.retry_job(owner, j["id"])
                except EZError:
                    pass
        self.store.set_batch_zip(batch_id, None)
        return self.batch_summary(owner, batch_id)

    def delete_batch(self, owner: str, batch_id: str) -> None:
        b = self.get_batch(owner, batch_id)
        jobs = self.store.batch_jobs(batch_id)
        if any(not JobState(j["state"]).terminal for j in jobs):
            raise EZError(ErrorCode.INVALID_STATE, "Cancel the batch before deleting it.")
        for j in jobs:
            self._purge_job(j)
        if b["zip_artifact_id"]:
            self.storage.remove_artifact(b["zip_artifact_id"])
            self.store.delete_artifact(b["zip_artifact_id"])
        with self.store.tx() as db:
            db.execute("DELETE FROM batches WHERE id=?", (batch_id,))

    def build_batch_archive(self, owner: str, batch_id: str) -> dict[str, Any]:
        b = self.get_batch(owner, batch_id)
        jobs = self.store.batch_jobs(batch_id)
        if any(not JobState(j["state"]).terminal for j in jobs):
            raise EZError(
                ErrorCode.INVALID_STATE, "The batch is still running. Wait for it to finish or cancel it."
            )
        ok = [j for j in jobs if j["state"] == "SUCCEEDED"]
        if not ok:
            raise EZError(
                ErrorCode.INVALID_STATE,
                "No file in this batch converted successfully, so there is nothing to download.",
            )
        if b["zip_artifact_id"]:
            art = self.store.get_artifact(b["zip_artifact_id"])
            if art and Path(art["path"]).exists():
                return art
        names = dedupe_names(
            [output_filename(j["original_name"], j["target_format"]) for j in ok], reserved=("manifest.json",)
        )
        arc_of = {j["id"]: n for j, n in zip(ok, names)}
        entries = []
        for j in jobs:
            res = j.get("result") or {}
            entries.append(
                {
                    "original_name": j["original_name"],
                    "source_format": j["source_format"],
                    "target_format": j["target_format"],
                    "status": j["state"],
                    "output_name": arc_of.get(j["id"]),
                    "size": res.get("size"),
                    "sha256": res.get("sha256"),
                    "fidelity": res.get("fidelity"),
                    "warnings": j.get("warnings") or [],
                    "error": (
                        {"code": j["error"]["code"], "message": j["error"]["message"]}
                        if j.get("error")
                        else None
                    ),
                }
            )
        manifest = {
            "generator": f"EZConverter {__version__}",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "batch_id": batch_id,
            "summary": {
                "total": len(jobs),
                "succeeded": len(ok),
                "failed": sum(j["state"] == "FAILED" for j in jobs),
                "cancelled": sum(j["state"] == "CANCELLED" for j in jobs),
            },
            "entries": entries,
        }
        aid = new_id()
        dest = self.storage.artifact_path(aid, "batch.zip")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for j in ok:
                art = self.store.get_artifact(j["artifact_id"])
                if not art or not Path(art["path"]).exists():
                    raise EZError(ErrorCode.NOT_FOUND, "An output of this batch has expired.")
                ctype = zipfile.ZIP_STORED if j["target_format"] in NON_COMPRESSIBLE else zipfile.ZIP_DEFLATED
                zf.write(art["path"], arc_of[j["id"]], compress_type=ctype)
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        now = time.time()
        rec = {
            "id": aid,
            "owner": owner,
            "job_id": None,
            "batch_id": batch_id,
            "name": f"ezconverter-batch-{batch_id[:8]}.zip",
            "path": str(dest),
            "size": dest.stat().st_size,
            "sha256": _sha(dest),
            "mime": "application/zip",
            "kind": "batch_zip",
            "created_at": now,
            "expires_at": now + self.s.retention_hours * 3600,
        }
        self.store.add_artifact(rec)
        self.store.set_batch_zip(batch_id, aid)
        return rec

    # ------------------------------------------------------------------ artifacts

    def get_artifact(self, owner: str, artifact_id: str) -> dict[str, Any]:
        a = self.store.get_artifact(artifact_id)
        if not a or a["owner"] != owner or not Path(a["path"]).exists():
            raise EZError(ErrorCode.NOT_FOUND, "Result not found or expired.")
        return a

    # ------------------------------------------------------------------ idempotency

    def idempotent(self, owner: str, key: str | None, scope: str, body: Any, create):  # noqa: ANN001
        if not key:
            return create(), False
        h = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        with self._idem_lock:
            prior = self.store.idempotency_get(owner, key, scope)
            if prior:
                if prior["request_hash"] != h:
                    raise EZError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "This Idempotency-Key was already used with a different request.",
                    )
                return prior["resource_id"], True
            res = create()
            rid = res["id"] if isinstance(res, dict) else res
            self.store.idempotency_put(owner, key, scope, h, rid)
            return res, False

    # ------------------------------------------------------------------ dispatcher

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._cv:
                    self._fill_slots()
                    self._cv.wait(timeout=0.25)
            except Exception:  # keep the dispatcher alive no matter what
                log.exception("dispatcher error")
                time.sleep(0.5)

    def _fill_slots(self) -> None:
        free = self.s.max_concurrency - len(self._running)
        if free <= 0:
            return
        for cand in self.store.queued_jobs(free * 4):
            if len(self._running) >= self.s.max_concurrency:
                break
            est = int((cand.get("plan") or {}).get("estimate", {}).get("memory_mb", 256))
            used = sum(self._running.values())
            if self._running and used + est > self.mem_budget_mb:
                continue  # wait for memory; a lone oversized job still runs
            job = self.store.claim(cand["id"])
            if job is None:
                continue
            self._running[job["id"]] = est
            self._pool.submit(self._run_job, job)

    def _run_job(self, job: dict[str, Any]) -> None:
        jid = job["id"]
        cancel = threading.Event()
        with self._cancel_lock:
            self._cancel[jid] = cancel
        # Registered first, checked second: a cancel_job() that wrote the flag after this read is
        # guaranteed to find the event, and one that wrote it before is seen here (the claim snapshot is
        # stale). Likewise a stop() whose snapshot of the events missed this job has already set _stop.
        cur = self.store.get_job(jid)
        if (cur or job)["cancel_requested"] or self._stop.is_set():
            cancel.set()
        attempt_dir = self.storage.job_dir(jid, job["attempts"])
        try:
            f = self.store.get_file(job["file_id"])
            if f is None:
                raise EZError(ErrorCode.NOT_FOUND, "The source file is no longer available.")
            plan = job["plan"]
            last_write = [0.0]
            state = {"verifying": False}

            def on_event(ev: dict[str, Any]) -> None:
                if ev.get("event") == "phase":
                    ph = ev.get("phase")
                    fields: dict[str, Any] = {"phase": ph}
                    if ph == "verifying" and ev.get("step") == ev.get("steps") and not state["verifying"]:
                        state["verifying"] = self.store.transition(
                            jid,
                            JobState.VERIFYING,
                            expect={JobState.RUNNING},
                            note="verifying output",
                            **fields,
                        )
                        return
                    self.store.update_job(jid, **fields)
                elif ev.get("event") == "progress":
                    now = time.monotonic()
                    if now - last_write[0] > 0.4:
                        last_write[0] = now
                        self.store.update_job(jid, progress=ev.get("fraction"), phase=ev.get("message"))

            req = {
                "op": "convert",
                "input": {
                    "path": f["path"],
                    "filename": f["original_name"],
                    "format": f["format"],
                    "size": f["size"],
                    "details": f["detection"].get("details", {}),
                },
                "steps": [
                    {"capability_id": s["capability_id"], "options": s["options"]} for s in plan["steps"]
                ],
                "output_dir": str(attempt_dir / "out"),
                "work_dir": str(attempt_dir / "work"),
                "output_stem": "result",
                "threads": self.s.engine_threads,
                "limits": {
                    "wall_seconds": self.s.job_timeout_s,
                    "cpu_seconds": self.s.worker_cpu_s,
                    "memory_mb": self.s.worker_memory_mb,
                    "output_file_mb": self.s.worker_output_mb,
                    "isolate_network": self.s.isolate_network,
                },
            }
            result = run_worker(req, attempt_dir, self.limits(), cancel, on_event)
            if cancel.is_set() and not result.get("ok"):
                raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
            if result.get("ok"):
                self._finish_success(job, f, plan, result, attempt_dir)
            else:
                e = result["error"]
                raise EZError(ErrorCode(e["code"]), e["message"], e.get("details"))
        except EZError as exc:
            self._finish_error(job, exc)
        except Exception:
            log.exception("job %s crashed in the runner", jid)
            self._finish_error(
                job, EZError(ErrorCode.INTERNAL, "An unexpected error occurred while converting this file.")
            )
        finally:
            self.storage.remove_tree(attempt_dir)
            with self._cancel_lock:
                self._cancel.pop(jid, None)
            with self._cv:
                self._running.pop(jid, None)
                self._cv.notify_all()

    def _finish_success(
        self,
        job: dict[str, Any],
        f: dict[str, Any],
        plan: dict[str, Any],
        result: dict[str, Any],
        attempt_dir: Path,
    ) -> None:
        jid = job["id"]
        out = Path(result["output_path"]).resolve()
        if (attempt_dir.resolve() not in out.parents) or not out.is_file():
            raise EZError(ErrorCode.INTERNAL, "The worker reported an invalid output location.")
        self.store.transition(jid, JobState.VERIFYING, expect={JobState.RUNNING}, note="verifying output")
        aid = new_id()
        dest = self.storage.artifact_path(aid, "x")
        shutil.move(str(out), dest)
        now = time.time()
        name = output_filename(job["original_name"], job["target_format"])
        self.store.add_artifact(
            {
                "id": aid,
                "owner": job["owner"],
                "job_id": jid,
                "batch_id": job["batch_id"],
                "name": name,
                "path": str(dest),
                "size": result["size"],
                "sha256": result["sha256"],
                "mime": FORMATS[job["target_format"]].mime,
                "kind": "output",
                "created_at": now,
                "expires_at": now + self.s.retention_hours * 3600,
            }
        )
        provenance = {
            "ezconverter_version": __version__,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "input_sha256": f["sha256"],
            "output_sha256": result["sha256"],
            "steps": plan["steps"],
            "tool_versions": result["tool_versions"],
            "duration_seconds": result["duration_seconds"],
            "attempt": job["attempts"],
            "runtime_location": self.s.location.value,
        }
        res = {
            "artifact_id": aid,
            "name": name,
            "size": result["size"],
            "sha256": result["sha256"],
            "mime": FORMATS[job["target_format"]].mime,
            "fidelity": plan["fidelity"],
            "verification": result["verification"],
            "provenance": provenance,
        }
        ok = self.store.transition(
            jid,
            JobState.SUCCEEDED,
            expect={JobState.VERIFYING},
            note="verified",
            result=res,
            artifact_id=aid,
            progress=1.0,
            phase="done",
            warnings=list(dict.fromkeys([*plan["warnings"], *result["warnings"]])),
            error=None,
        )
        if not ok:  # lost a race with cancellation/recovery: never expose an orphan
            self.storage.remove_artifact(aid)
            self.store.delete_artifact(aid)

    def _finish_error(self, job: dict[str, Any], exc: EZError) -> None:
        jid = job["id"]
        cur = self.store.get_job(jid)
        if cur is None or JobState(cur["state"]).terminal:
            return
        if exc.code is ErrorCode.CANCELLED and self._stop.is_set() and not cur["cancel_requested"]:
            # Killed by shutdown, not by the user: hand the job back to the queue so the next start runs
            # it. The interrupted attempt is refunded; it was not the job's fault.
            self.store.transition(
                jid,
                JobState.QUEUED,
                note="re-queued on shutdown",
                expect={JobState.RUNNING, JobState.VERIFYING},
                attempts=max(cur["attempts"] - 1, 0),
                progress=None,
                phase=None,
            )
            return
        if exc.code is ErrorCode.CANCELLED or cur["cancel_requested"]:
            self.store.transition(
                jid,
                JobState.CANCELLED,
                note="cancelled",
                expect={JobState.RUNNING, JobState.VERIFYING},
                error=_err(ErrorCode.CANCELLED, "The conversion was cancelled.", retryable=False),
            )
            return
        if exc.code is ErrorCode.WORKER_LOST and cur["attempts"] < cur["max_attempts"]:
            self.store.transition(
                jid,
                JobState.QUEUED,
                note="worker lost; retrying automatically",
                expect={JobState.RUNNING, JobState.VERIFYING},
            )
            return
        self.store.transition(
            jid,
            JobState.FAILED,
            note=exc.code.value,
            expect={JobState.RUNNING, JobState.VERIFYING},
            error=_err(exc.code, exc.message, exc.details, retryable=exc.retryable),
            progress=None,
        )

    # ------------------------------------------------------------------ retention

    def _sweep_loop(self) -> None:
        while not self._stop.wait(self.s.sweep_interval_s):
            try:
                self.sweep()
            except Exception:
                log.exception("sweep failed")

    def sweep(self, now: float | None = None) -> dict[str, int]:
        now = now or time.time()
        removed = {"jobs": 0, "files": 0, "artifacts": 0, "batches": 0}
        for j in self.store.q("SELECT * FROM jobs WHERE expires_at < ?", (now,)):
            if JobState(j["state"]).terminal:
                self._purge_job(j)
                removed["jobs"] += 1
        for a in self.store.q("SELECT * FROM artifacts WHERE expires_at < ?", (now,)):
            self.storage.remove_artifact(a["id"])
            self.store.delete_artifact(a["id"])
            removed["artifacts"] += 1
            if a["kind"] == "batch_zip" and a["batch_id"]:
                self.store.set_batch_zip(a["batch_id"], None)
        for b in self.store.q("SELECT id FROM batches WHERE expires_at < ?", (now,)):
            if not self.store.batch_jobs(b["id"]):
                with self.store.tx() as db:
                    db.execute("DELETE FROM batches WHERE id=?", (b["id"],))
                removed["batches"] += 1
        for f in self.store.q("SELECT id FROM files WHERE expires_at < ?", (now,)):
            busy = self.store.q(
                "SELECT 1 FROM jobs WHERE file_id=? AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED') LIMIT 1",
                (f["id"],),
            )
            if not busy:
                self.storage.remove_upload(f["id"])
                self.store.delete_file(f["id"])
                removed["files"] += 1
        return removed


def _err(
    code: ErrorCode, message: str, details: dict[str, Any] | None = None, retryable: bool | None = None
) -> dict[str, Any]:
    from ..domain.errors import RETRYABLE

    return {
        "code": code.value,
        "message": message,
        "details": details or {},
        "retryable": (code in RETRYABLE) if retryable is None else retryable,
    }


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while c := fh.read(1024 * 1024):
            h.update(c)
    return h.hexdigest()
