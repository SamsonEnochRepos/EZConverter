"""SQLite persistence for uploads, jobs, batches, artifacts and an audit trail of state changes.

One connection guarded by a lock keeps the semantics simple (WAL mode, short transactions).
All state transitions go through `transition`, which enforces the lifecycle rules and records an
event row. Job state is durable, so the manager can recover after a restart.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..domain.errors import ErrorCode, EZError
from ..domain.states import JobState, assert_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, original_name TEXT NOT NULL, path TEXT NOT NULL,
  size INTEGER NOT NULL, sha256 TEXT NOT NULL, format TEXT, detection TEXT NOT NULL,
  created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS files_owner ON files(owner);
CREATE TABLE IF NOT EXISTS batches(
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
  zip_artifact_id TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, batch_id TEXT, file_id TEXT NOT NULL,
  original_name TEXT NOT NULL, source_format TEXT NOT NULL, target_format TEXT NOT NULL,
  options TEXT NOT NULL, policy TEXT NOT NULL, plan TEXT, state TEXT NOT NULL,
  progress REAL, phase TEXT, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
  error TEXT, result TEXT, warnings TEXT NOT NULL DEFAULT '[]', cancel_requested INTEGER NOT NULL DEFAULT 0,
  artifact_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL,
  finished_at REAL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, created_at);
CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner);
CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch_id);
CREATE TABLE IF NOT EXISTS job_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts REAL NOT NULL,
  from_state TEXT, to_state TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS artifacts(
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, job_id TEXT, batch_id TEXT, name TEXT NOT NULL,
  path TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, mime TEXT NOT NULL,
  kind TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS artifacts_owner ON artifacts(owner);
CREATE TABLE IF NOT EXISTS idempotency(
  owner TEXT NOT NULL, key TEXT NOT NULL, scope TEXT NOT NULL, request_hash TEXT NOT NULL,
  resource_id TEXT NOT NULL, PRIMARY KEY(owner, key, scope));
"""

_JSON_COLS = {"detection", "options", "plan", "error", "result", "warnings"}


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in _JSON_COLS & d.keys():
        if d[k] is not None:
            d[k] = json.loads(d[k])
    return d


class Store:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def q(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [_row(r) for r in self._db.execute(sql, args).fetchall()]  # type: ignore[misc]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._db.execute(sql, args).fetchone())

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ files

    def add_file(self, rec: dict[str, Any]) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO files VALUES(:id,:owner,:original_name,:path,:size,:sha256,:format,:detection,:created_at,:expires_at)",
                {**rec, "detection": json.dumps(rec["detection"])},
            )

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM files WHERE id=?", (file_id,))

    def delete_file(self, file_id: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM files WHERE id=?", (file_id,))

    def owner_usage(self, owner: str) -> int:
        a = self.one("SELECT COALESCE(SUM(size),0) s FROM files WHERE owner=?", (owner,))["s"]  # type: ignore[index]
        b = self.one("SELECT COALESCE(SUM(size),0) s FROM artifacts WHERE owner=?", (owner,))["s"]  # type: ignore[index]
        return int(a) + int(b)

    # ------------------------------------------------------------------ jobs

    def add_job(self, rec: dict[str, Any], note: str = "created") -> None:
        now = time.time()
        row = {
            "plan": None,
            "error": None,
            "result": None,
            "progress": None,
            "phase": None,
            "attempts": 0,
            "batch_id": None,
            "artifact_id": None,
            "started_at": None,
            "finished_at": None,
            "cancel_requested": 0,
            **rec,
            "created_at": now,
            "updated_at": now,
        }
        for k in ("options", "plan", "error", "result", "warnings"):
            if row.get(k) is not None:
                row[k] = json.dumps(row[k])
        row.setdefault("warnings", "[]")
        with self.tx() as db:
            db.execute(
                "INSERT INTO jobs(id,owner,batch_id,file_id,original_name,source_format,target_format,options,policy,plan,"
                "state,progress,phase,attempts,max_attempts,error,result,warnings,cancel_requested,artifact_id,created_at,"
                "updated_at,started_at,finished_at,expires_at) VALUES(:id,:owner,:batch_id,:file_id,:original_name,"
                ":source_format,:target_format,:options,:policy,:plan,:state,:progress,:phase,:attempts,:max_attempts,"
                ":error,:result,:warnings,:cancel_requested,:artifact_id,:created_at,:updated_at,:started_at,:finished_at,"
                ":expires_at)",
                row,
            )
            db.execute(
                "INSERT INTO job_events(job_id,ts,from_state,to_state,note) VALUES(?,?,?,?,?)",
                (row["id"], now, None, row["state"], note),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def transition(
        self,
        job_id: str,
        dst: JobState,
        *,
        expect: set[JobState] | None = None,
        note: str = "",
        **fields: Any,
    ) -> bool:
        """Atomically move a job to `dst`. Returns False if the job is no longer in an expected state."""
        now = time.time()
        with self.tx() as db:
            row = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise EZError(ErrorCode.NOT_FOUND, "Job not found.")
            src = JobState(row["state"])
            if expect is not None and src not in expect:
                return False
            assert_transition(src, dst)
            sets, args = ["state=?", "updated_at=?"], [dst.value, now]
            for k, v in fields.items():
                sets.append(f"{k}=?")
                args.append(json.dumps(v) if k in _JSON_COLS and v is not None else v)
            if dst.terminal:
                sets.append("finished_at=?")
                args.append(now)
            db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (*args, job_id))
            db.execute(
                "INSERT INTO job_events(job_id,ts,from_state,to_state,note) VALUES(?,?,?,?,?)",
                (job_id, now, src.value, dst.value, note),
            )
        return True

    def update_job(self, job_id: str, **fields: Any) -> None:
        sets = [f"{k}=?" for k in fields] + ["updated_at=?"]
        args = [json.dumps(v) if k in _JSON_COLS and v is not None else v for k, v in fields.items()] + [
            time.time()
        ]
        with self.tx() as db:
            db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (*args, job_id))

    def queued_jobs(self, limit: int) -> list[dict[str, Any]]:
        return self.q("SELECT * FROM jobs WHERE state='QUEUED' ORDER BY created_at, id LIMIT ?", (limit,))

    def claim(self, job_id: str) -> dict[str, Any] | None:
        """Atomically move one QUEUED job to RUNNING (attempts+1). None if someone else got it first."""
        now = time.time()
        with self.tx() as db:
            cur = db.execute(
                "UPDATE jobs SET state='RUNNING', attempts=attempts+1, started_at=?, updated_at=?, phase='running', "
                "progress=NULL WHERE id=? AND state='QUEUED'",
                (now, now, job_id),
            )
            if cur.rowcount != 1:
                return None
            db.execute(
                "INSERT INTO job_events(job_id,ts,from_state,to_state,note) VALUES(?,?,?,?,?)",
                (job_id, now, "QUEUED", "RUNNING", "claimed"),
            )
        return self.get_job(job_id)

    def jobs_in_states(self, states: list[str]) -> list[dict[str, Any]]:
        ph = ",".join("?" * len(states))
        return self.q(f"SELECT * FROM jobs WHERE state IN ({ph})", tuple(states))

    def batch_jobs(self, batch_id: str) -> list[dict[str, Any]]:
        return self.q("SELECT * FROM jobs WHERE batch_id=? ORDER BY created_at, id", (batch_id,))

    def job_events(self, job_id: str) -> list[dict[str, Any]]:
        return self.q(
            "SELECT ts,from_state,to_state,note FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
        )

    # ------------------------------------------------------------------ batches / artifacts / idempotency

    def add_batch(self, rec: dict[str, Any]) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO batches(id,owner,created_at,expires_at,zip_artifact_id) VALUES(:id,:owner,:created_at,:expires_at,NULL)",
                rec,
            )

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM batches WHERE id=?", (batch_id,))

    def set_batch_zip(self, batch_id: str, artifact_id: str | None) -> None:
        with self.tx() as db:
            db.execute("UPDATE batches SET zip_artifact_id=? WHERE id=?", (artifact_id, batch_id))

    def add_artifact(self, rec: dict[str, Any]) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO artifacts VALUES(:id,:owner,:job_id,:batch_id,:name,:path,:size,:sha256,:mime,:kind,:created_at,:expires_at)",
                rec,
            )

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))

    def delete_artifact(self, artifact_id: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))

    def idempotency_get(self, owner: str, key: str, scope: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM idempotency WHERE owner=? AND key=? AND scope=?", (owner, key, scope))

    def idempotency_put(self, owner: str, key: str, scope: str, request_hash: str, resource_id: str) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR IGNORE INTO idempotency VALUES(?,?,?,?,?)",
                (owner, key, scope, request_hash, resource_id),
            )
