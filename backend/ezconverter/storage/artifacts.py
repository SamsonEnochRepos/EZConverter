"""Local-filesystem artifact storage. All paths are derived from server-generated ids only."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol


def new_id() -> str:
    return uuid.uuid4().hex


class ArtifactStore(Protocol):
    def upload_path(self, file_id: str) -> Path: ...
    def job_dir(self, job_id: str, attempt: int) -> Path: ...
    def artifact_path(self, artifact_id: str, name: str) -> Path: ...
    def remove_tree(self, path: Path) -> None: ...


class LocalArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        for d in ("uploads", "jobs", "artifacts", "tmp"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def _within(self, p: Path) -> Path:
        rp = p.resolve()
        if self.root not in rp.parents and rp != self.root:
            raise ValueError("path escapes storage root")
        return rp

    def upload_path(self, file_id: str) -> Path:
        self._check_id(file_id)
        d = self._within(self.root / "uploads" / file_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / "blob"

    def job_dir(self, job_id: str, attempt: int) -> Path:
        self._check_id(job_id)
        d = self._within(self.root / "jobs" / job_id / f"attempt{int(attempt)}")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def artifact_path(self, artifact_id: str, name: str) -> Path:
        self._check_id(artifact_id)
        d = self._within(self.root / "artifacts" / artifact_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / "blob"  # the display name lives in the database, never on disk

    def tmp_dir(self) -> Path:
        d = self.root / "tmp" / new_id()
        d.mkdir(parents=True)
        return d

    @staticmethod
    def _check_id(i: str) -> None:
        if not i or len(i) > 64 or not all(c in "0123456789abcdef" for c in i):
            raise ValueError("invalid id")

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(self._within(path), ignore_errors=True)

    def remove_upload(self, file_id: str) -> None:
        self.remove_tree(self.root / "uploads" / file_id)

    def remove_artifact(self, artifact_id: str) -> None:
        self.remove_tree(self.root / "artifacts" / artifact_id)

    def remove_job(self, job_id: str) -> None:
        self.remove_tree(self.root / "jobs" / job_id)

    def dir_size(self, path: Path) -> int:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def copy_stream_limited(
    src: BinaryIO, dst_path: Path, limit_bytes: int, chunk: int = 1024 * 1024
) -> tuple[int, str]:
    """Copy src to dst_path, enforcing a size limit while streaming. Returns (size, sha256)."""
    h = hashlib.sha256()
    size = 0
    with open(dst_path, "wb") as out:
        while True:
            buf = src.read(chunk)
            if not buf:
                break
            size += len(buf)
            if size > limit_bytes:
                raise OverflowError("size limit exceeded")
            h.update(buf)
            out.write(buf)
    return size, h.hexdigest()
