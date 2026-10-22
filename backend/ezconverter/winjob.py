"""Windows Job Object confinement for worker processes (the Windows counterpart of prlimit).

A job caps committed memory and CPU time, limits how many processes the worker tree may create,
blocks desktop/clipboard/system-parameter access, and kills every member when the job is closed or
terminated. It does NOT isolate the network: Windows has no unprivileged equivalent of `unshare -n`.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# READCLIPBOARD | WRITECLIPBOARD | SYSTEMPARAMETERS | DISPLAYSETTINGS | GLOBALATOMS | DESKTOP | EXITWINDOWS
_UI_RESTRICTIONS = 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x40 | 0x80
_EXTENDED_LIMIT_INFORMATION = 9
_BASIC_UI_RESTRICTIONS = 4

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
# Enough for LibreOffice (soffice.exe + soffice.bin) and ffmpeg helpers, far below a fork bomb.
MAX_ACTIVE_PROCESSES = 16


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _UiRestrictions(ctypes.Structure):
    _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]


@dataclass
class JobHandle:
    handle: int

    def terminate(self) -> None:
        _k32().TerminateJobObject(wintypes.HANDLE(self.handle), 1)

    def close(self) -> None:
        if self.handle:
            _k32().CloseHandle(wintypes.HANDLE(self.handle))
            self.handle = 0


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def confine(process_handle: int, *, memory_mb: int, cpu_seconds: int) -> JobHandle:
    """Put a (suspended) process into a new limited job. Raises OSError if any step fails."""
    if sys.platform != "win32":
        raise OSError("Job Objects exist only on Windows")
    k = _k32()
    job = k.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_JOB_TIME
            | _JOB_OBJECT_LIMIT_JOB_MEMORY
            | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info.BasicLimitInformation.ActiveProcessLimit = MAX_ACTIVE_PROCESSES
        info.BasicLimitInformation.PerJobUserTimeLimit = cpu_seconds * 10_000_000  # 100 ns units
        info.JobMemoryLimit = memory_mb * 1024 * 1024
        if not k.SetInformationJobObject(job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        ui = _UiRestrictions(_UI_RESTRICTIONS)
        if not k.SetInformationJobObject(job, _BASIC_UI_RESTRICTIONS, ctypes.byref(ui), ctypes.sizeof(ui)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not k.AssignProcessToJobObject(job, wintypes.HANDLE(process_handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        k.CloseHandle(job)
        raise
    return JobHandle(int(job))


def resume(process_handle: int) -> None:
    """Resume a process created with CREATE_SUSPENDED (NtResumeProcess resumes all of its threads)."""
    status = ctypes.WinDLL("ntdll").NtResumeProcess(wintypes.HANDLE(process_handle))
    if status != 0:
        raise OSError(f"NtResumeProcess failed with NTSTATUS {status & 0xFFFFFFFF:#x}")
