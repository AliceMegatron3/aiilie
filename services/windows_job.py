"""Windows Job Object adapter for bounded child-process lifetimes.

The module is importable on every platform. Non-Windows reports an explicit
unavailable profile instead of pretending that process.kill is a sandbox.
"""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Any


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
_JOB_OBJECT_LIMIT_WORKINGSET = 0x00000001
_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_MORE_DATA = 234


def _ctypes_available() -> bool:
    """Windows 且 ctypes.windll.kernel32 可用（CI/沙箱不可用时 false）。"""
    return os.name == "nt" and getattr(ctypes, "windll", None) is not None


def enumerate_job_processes(job_handle: int) -> list[int]:
    """枚举 Job Object 中当前被分配的所有进程 PID。

    通过 QueryInformationJobObject(JobObjectBasicProcessIdList) 查询，
    覆盖整棵进程树（Job 的进程成员自动包含所有后代入池子进程）。
    非 Windows 或句柄无效时返回空列表。
    """
    if not job_handle or not _ctypes_available():
        return []
    kernel32 = ctypes.windll.kernel32
    header_size = 2 * ctypes.sizeof(ctypes.c_uint32)
    pid_size = ctypes.sizeof(ctypes.c_size_t)
    max_pids = 256
    while True:
        buffer = ctypes.create_string_buffer(header_size + max_pids * pid_size)
        returned = ctypes.c_uint32(0)
        ok = kernel32.QueryInformationJobObject(
            ctypes.c_void_p(job_handle),
            _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
            buffer,
            ctypes.sizeof(buffer),
            ctypes.byref(returned),
        )
        if not ok:
            if ctypes.get_last_error() == _ERROR_MORE_DATA and max_pids < 65536:
                max_pids *= 2
                continue
            return []
        num_assigned = int.from_bytes(buffer.raw[0:4], "little")
        pids: list[int] = []
        for index in range(num_assigned):
            offset = header_size + index * pid_size
            pids.append(int.from_bytes(buffer.raw[offset : offset + pid_size], "little"))
        return pids


def kill_job(job_handle: int) -> list[int]:
    """终止 Job Object 内所有进程（进程树收尸），返回已终止的 PID 列表。"""
    if not job_handle or not _ctypes_available():
        return []
    kernel32 = ctypes.windll.kernel32
    killed: list[int] = []
    for pid in enumerate_job_processes(job_handle):
        handle = kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            continue
        try:
            if kernel32.TerminateProcess(ctypes.c_void_p(handle), 1):
                killed.append(pid)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    return killed


@dataclass(frozen=True)
class JobLimits:
    max_processes: int | None = None
    memory_bytes: int | None = None
    cpu_time_100ns: int | None = None


class JobObjectError(RuntimeError):
    pass


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsJobObject:
    def __init__(self, limits: JobLimits | None = None) -> None:
        self.limits = limits or JobLimits()
        self.handle: int | None = None
        self.available = os.name == "nt"
        self.degraded_reason = "" if self.available else "windows_only"
        self.enforced_limits: dict[str, int | bool] = {}

    def create(self) -> None:
        if not self.available:
            return
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise JobObjectError(f"CreateJobObjectW failed: {ctypes.get_last_error()}")
        self.handle = int(handle)
        try:
            self._set_limits()
        except Exception:
            self.close()
            raise

    def _set_limits(self) -> None:
        if self.handle is None:
            raise JobObjectError("Job Object has not been created")
        info = _ExtendedLimitInformation()
        flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self.enforced_limits = {"kill_on_close": True}
        if self.limits.max_processes is not None:
            info.BasicLimitInformation.ActiveProcessLimit = int(self.limits.max_processes)
            flags |= _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            self.enforced_limits["max_processes"] = int(self.limits.max_processes)
        if self.limits.memory_bytes is not None:
            info.ProcessMemoryLimit = int(self.limits.memory_bytes)
            flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            self.enforced_limits["memory_bytes"] = int(self.limits.memory_bytes)
        if self.limits.cpu_time_100ns is not None:
            info.BasicLimitInformation.PerProcessUserTimeLimit = int(self.limits.cpu_time_100ns)
            flags |= _JOB_OBJECT_LIMIT_PROCESS_TIME
            self.enforced_limits["cpu_time_100ns"] = int(self.limits.cpu_time_100ns)
        info.BasicLimitInformation.LimitFlags = flags
        if not ctypes.windll.kernel32.SetInformationJobObject(
            ctypes.c_void_p(self.handle),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise JobObjectError(f"SetInformationJobObject failed: {ctypes.get_last_error()}")

    def assign(self, pid: int) -> None:
        if not self.available or self.handle is None:
            return
        kernel32 = ctypes.windll.kernel32
        process_handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            int(pid),
        )
        if not process_handle:
            raise JobObjectError(f"OpenProcess failed: {ctypes.get_last_error()}")
        try:
            if not kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(self.handle), ctypes.c_void_p(process_handle)
            ):
                raise JobObjectError(f"AssignProcessToJobObject failed: {ctypes.get_last_error()}")
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(process_handle))

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None

    def __enter__(self) -> "WindowsJobObject":
        self.create()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
