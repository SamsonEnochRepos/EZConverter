"""Sandboxed worker entry point.

    python -I -m ezconverter.workers.entry <request.json>

Writes JSON-line events to the original stdout; everything else (library chatter) goes to stderr.
Runs one operation ("inspect" or "convert") and exits. The launcher is responsible for resource
limits and network isolation.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from typing import Any


def main(argv: list[str]) -> int:
    events = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr  # keep the event channel clean

    def emit(obj: dict[str, Any]) -> None:
        events.write(json.dumps(obj, separators=(",", ":")) + "\n")
        events.flush()

    cancel = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: cancel.set())

    try:
        with open(argv[1], encoding="utf-8") as fh:
            req = json.load(fh)
        op = req["op"]
        if op == "inspect":
            from ..detection import inspect_file

            det = inspect_file(req["path"], req["filename"], req.get("work_dir"))
            emit({"event": "result", "ok": True, "detection": det.to_dict()})
            return 0
        if op == "convert":
            from ..registry import default_registry
            from .pipeline import error_payload, run_plan

            registry = default_registry()
            try:
                result = run_plan(req, registry, emit, cancel)
            except BaseException as exc:  # noqa: BLE001 - everything becomes a structured error
                result = error_payload(exc)
            emit({"event": "result", **result})
            return 0
        emit(
            {
                "event": "result",
                "ok": False,
                "error": {"code": "invalid_request", "message": "unknown op", "details": {}},
            }
        )
        return 2
    except BaseException as exc:  # noqa: BLE001
        from .pipeline import error_payload

        emit({"event": "result", **error_payload(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
