"""Starts sandboxed worker processes and translates their outcome into domain results."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..domain.errors import ErrorCode, EZError
from ..sandbox import Limits, run_process

# SIGXCPU/SIGXFSZ do not exist on Windows.
_LIMIT_SIGNALS = {getattr(signal, n) for n in ("SIGXCPU", "SIGKILL", "SIGXFSZ") if hasattr(signal, n)}

EventFn = Callable[[dict[str, Any]], None]


# The only variables forwarded into the otherwise scrubbed worker environment.
FORWARDED_ENV = ("EZ_EXTRA_ENGINES",)


def worker_env() -> dict[str, str]:
    return {k: os.environ[k] for k in FORWARDED_ENV if k in os.environ}


def run_worker(
    request: dict[str, Any],
    workdir: Path,
    limits: Limits,
    cancel: threading.Event | None = None,
    on_event: EventFn | None = None,
) -> dict[str, Any]:
    """Run one worker op and return its final `result` event.

    Raises EZError for: cancellation, timeout, worker crash/limit kill. Engine-level failures are
    returned as `{"ok": False, "error": {...}}` and are the caller's to interpret.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    req_file = workdir / "request.json"
    req_file.write_text(json.dumps(request), encoding="utf-8")
    final: dict[str, Any] = {}

    def handle(line: str) -> None:
        if not line.startswith("{"):
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if ev.get("event") == "result":
            final.update(ev)
        elif on_event:
            on_event(ev)

    if getattr(sys, "frozen", False):  # packaged build: the executable itself serves the `worker` command
        argv = [sys.executable, "worker", str(req_file)]
    else:
        argv = [sys.executable, "-I", "-m", "ezconverter.workers.entry", str(req_file)]
    r = run_process(
        argv,
        workdir=workdir,
        limits=limits,
        cancel=cancel,
        on_line=handle,
        capture_stdout=False,
        env_extra=worker_env(),
    )
    if r.cancelled:
        raise EZError(ErrorCode.CANCELLED, "The conversion was cancelled.")
    if r.timed_out:
        raise EZError(ErrorCode.TIMEOUT, "The conversion took too long and was stopped.")
    if final:
        return final
    sig = r.killed_by_signal
    if sig in _LIMIT_SIGNALS:
        raise EZError(ErrorCode.RESOURCE_LIMIT, "The conversion exceeded its CPU, memory or disk limits.")
    if sig:
        raise EZError(
            ErrorCode.CONVERSION_FAILED,
            "The conversion engine crashed on this file.",
            {"signal": signal.Signals(sig).name},
        )
    raise EZError(ErrorCode.WORKER_LOST, "The worker stopped unexpectedly.", {"exit_code": r.returncode})
