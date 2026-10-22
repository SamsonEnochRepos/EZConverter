"""Sandboxed subprocess execution.

Every external tool and every parser of untrusted bytes runs through `run_process`:
argv lists only (never a shell), scrubbed environment, resource limits, optional network
namespace isolation, a wall-clock deadline, cooperative cancellation and process-group kill.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Limits:
    wall_seconds: float = 900.0
    cpu_seconds: int = 900
    memory_mb: int = 6144  # RLIMIT_AS; generous because LibreOffice maps a lot of address space
    output_file_mb: int = 4096  # RLIMIT_FSIZE: caps any single file a process can write
    isolate_network: bool = True


@dataclass
class ProcResult:
    returncode: int
    stdout: str = ""
    stderr_tail: str = ""
    timed_out: bool = False
    cancelled: bool = False
    killed_by_signal: int | None = None
    lines: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _netns_prefix() -> tuple[str, ...] | None:
    """Return an argv prefix that runs a command in an empty network namespace, if possible."""
    unshare = shutil.which("unshare")
    if not unshare:
        return None
    for prefix in ((unshare, "-n", "--"), (unshare, "-rn", "--")):
        try:
            r = subprocess.run([*prefix, "true"], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return tuple(prefix)
    return None


def network_isolation_available() -> bool:
    return _netns_prefix() is not None


def sandbox_prefix(limits: Limits) -> list[str]:
    prefix: list[str] = []
    prlimit = shutil.which("prlimit")
    if prlimit:
        prefix += [
            prlimit,
            f"--cpu={limits.cpu_seconds}",
            f"--as={limits.memory_mb * 1024 * 1024}",
            f"--fsize={limits.output_file_mb * 1024 * 1024}",
            "--core=0",
            "--",
        ]
    if limits.isolate_network:
        ns = _netns_prefix()
        if ns:
            prefix += list(ns)
    return prefix


def scrubbed_env(workdir: str | os.PathLike[str]) -> dict[str, str]:
    if sys.platform == "win32":
        # Windows has no fixed system bin dirs, and Python/DLL loading needs SYSTEMROOT; keep only what is required.
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT") if k in os.environ}
        return {**env, "HOME": str(workdir), "TMPDIR": str(workdir), "TEMP": str(workdir), "TMP": str(workdir)}
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def run_process(
    argv: Sequence[str],
    *,
    workdir: str | os.PathLike[str],
    limits: Limits | None = None,
    cancel: threading.Event | None = None,
    on_line: Callable[[str], None] | None = None,
    capture_stdout: bool = True,
    max_capture_bytes: int = 4 * 1024 * 1024,
    sandboxed: bool = True,
    env_extra: dict[str, str] | None = None,
) -> ProcResult:
    if isinstance(argv, str):  # defensive: a shell string must never reach here
        raise TypeError("argv must be a sequence of arguments, not a string")
    limits = limits or Limits()
    full = [*(sandbox_prefix(limits) if sandboxed else []), *map(str, argv)]
    proc = subprocess.Popen(
        full,
        cwd=str(workdir),
        env={**scrubbed_env(workdir), **(env_extra or {})},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out_chunks: list[str] = []
    out_lines: list[str] = []
    out_size = 0
    err_tail: deque[str] = deque(maxlen=60)

    def pump_out() -> None:
        nonlocal out_size
        assert proc.stdout
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if on_line:
                try:
                    on_line(line)
                except Exception:  # a broken callback must never wedge the pipe
                    pass
            if capture_stdout and out_size < max_capture_bytes:
                out_chunks.append(line + "\n")
                out_size += len(raw)
            if len(out_lines) < 5000:
                out_lines.append(line)

    def pump_err() -> None:
        assert proc.stderr
        for raw in iter(proc.stderr.readline, b""):
            err_tail.append(raw.decode("utf-8", "replace").rstrip("\n")[:2000])

    threads = [threading.Thread(target=pump_out, daemon=True), threading.Thread(target=pump_err, daemon=True)]
    for t in threads:
        t.start()

    deadline = time.monotonic() + limits.wall_seconds
    timed_out = cancelled = False
    while True:
        try:
            proc.wait(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            cancelled = True
        elif time.monotonic() > deadline:
            timed_out = True
        if cancelled or timed_out:
            _kill_group(proc)
            proc.wait()
            break
    for t in threads:
        t.join(timeout=2)
    rc = proc.returncode
    return ProcResult(
        returncode=rc,
        stdout="".join(out_chunks),
        stderr_tail="\n".join(err_tail),
        timed_out=timed_out,
        cancelled=cancelled,
        killed_by_signal=-rc if rc is not None and rc < 0 else None,
        lines=out_lines,
    )


def _kill_group(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":  # no process groups: kill the whole tree
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def which(name: str) -> str | None:
    return shutil.which(name)
