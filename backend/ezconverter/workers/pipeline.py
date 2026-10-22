"""Executes a validated plan: run each step, verify each output, hand the result to the next step.

This module runs *inside* the sandboxed worker process. It never trusts that an engine's exit
status means success: every step's output is verified before it is used or reported.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..detection.sniffer import inspect_file
from ..domain.errors import ErrorCode, EZError
from ..engines.base import ExecutionContext, InputInfo
from ..registry import CapabilityRegistry
from ..sandbox import Limits
from ..validation import verify_output

Emit = Callable[[dict[str, Any]], None]


def limits_from(d: dict[str, Any]) -> Limits:
    return Limits(**{k: v for k, v in d.items() if k in Limits.__dataclass_fields__})


def run_plan(
    req: dict[str, Any], registry: CapabilityRegistry, emit: Emit, cancel: threading.Event
) -> dict[str, Any]:
    limits = limits_from(req.get("limits", {}))
    inp = req["input"]
    work_root = Path(req["work_dir"])
    out_dir = Path(req["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = InputInfo(Path(inp["path"]), inp["filename"], inp["format"], inp["size"], inp.get("details", {}))
    steps = req["steps"]
    all_warnings: list[str] = []
    versions: dict[str, str] = {}
    step_reports: list[dict[str, Any]] = []
    started = time.time()

    for i, step in enumerate(steps):
        final = i == len(steps) - 1
        entry = registry.get(step["capability_id"])
        if entry is None or not entry.availability.ok:
            raise EZError(
                ErrorCode.ENGINE_UNAVAILABLE, "The conversion engine for this step is not available."
            )
        cap, adapter = entry.capability, entry.adapter
        step_work = work_root / f"step{i}"
        step_work.mkdir(parents=True, exist_ok=True)
        step_out = out_dir if final else step_work / "out"
        step_out.mkdir(parents=True, exist_ok=True)

        def progress(fraction: float | None, message: str | None, _i: int = i) -> None:
            overall = None if fraction is None else (_i + max(0.0, min(1.0, fraction))) / len(steps)
            emit(
                {
                    "event": "progress",
                    "fraction": overall,
                    "message": message,
                    "step": _i + 1,
                    "steps": len(steps),
                }
            )

        ctx = ExecutionContext(
            capability=cap,
            input=cur,
            options=step["options"],
            output_dir=step_out,
            work_dir=step_work,
            output_stem=req["output_stem"] if final else f"intermediate{i}",
            limits=limits,
            cancel=cancel,
            progress=progress,
            threads=req.get("threads", 2),
        )
        emit({"event": "phase", "phase": "running", "step": i + 1, "steps": len(steps), "capability": cap.id})
        result = adapter.execute(ctx)
        versions.update(result.tool_versions)
        all_warnings += result.warnings

        emit({"event": "phase", "phase": "verifying", "step": i + 1, "steps": len(steps)})
        extra = adapter.extra_checks(ctx, result)
        report = verify_output(
            target=cap.target,
            source=cur,
            options=step["options"],
            output=result.output_path,
            work_dir=step_work,
            extra=extra,
        )
        step_reports.append({"capability_id": cap.id, **report.to_dict()})
        if not report.ok:
            failed = "; ".join(f"{c.name}: {c.detail}" for c in report.failures())
            raise EZError(
                ErrorCode.OUTPUT_VALIDATION_FAILED,
                "The converted file did not pass validation and was discarded.",
                {"failed_checks": [c.to_dict() for c in report.failures()], "summary": failed[:500]},
            )
        if not final:
            det = inspect_file(str(result.output_path), result.output_path.name, str(step_work))
            cur = InputInfo(result.output_path, result.output_path.name, cap.target, report.size, det.details)
        else:
            final_path, final_report = result.output_path, report

    return {
        "ok": True,
        "output_path": str(final_path),
        "output_name": final_path.name,
        "size": final_report.size,
        "sha256": final_report.sha256,
        "warnings": list(dict.fromkeys(all_warnings)),
        "tool_versions": versions,
        "verification": final_report.to_dict(),
        "steps": step_reports,
        "duration_seconds": round(time.time() - started, 3),
    }


def error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, MemoryError):
        exc = EZError(ErrorCode.RESOURCE_LIMIT, "The conversion ran out of memory.")
    if isinstance(exc, EZError):
        return {
            "ok": False,
            "error": {
                "code": exc.code.value,
                "message": exc.message,
                "details": exc.details,
                "retryable": exc.retryable,
            },
        }
    return {
        "ok": False,
        "error": {
            "code": ErrorCode.INTERNAL.value,
            "message": "An unexpected error occurred while converting this file.",
            "details": {"exception": type(exc).__name__},
            "retryable": True,
        },
    }
