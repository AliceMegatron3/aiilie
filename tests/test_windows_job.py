"""跨平台 Job Object/输出限制契约测试。

Windows 上真实 spawn 子进程/孙进程，验证 Job Object 限制覆盖整棵进程树；
非 Windows 或 ctypes.windll 不可用（CI/沙箱）时跳过进程级用例。
所有进程用例都以"能清理、不悬挂"为底线：finally 里 kill 全部衍生 pid 并关闭 Job。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from services.windows_job import JobLimits, JobObjectError, WindowsJobObject, enumerate_job_processes, kill_job

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _windows_job_available() -> bool:
    return os.name == "nt" and getattr(ctypes, "windll", None) is not None


def _spawn_sleep(seconds: int = 60) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )


def _spawn_parent_with_grandchild(marker: Path) -> subprocess.Popen:
    """父进程等待 marker 出现后才 spawn 孙进程（保证孙进程进入 Job）。"""
    code = (
        "import subprocess, sys, time, os\n"
        f"marker = {str(marker)!r}\n"
        "while not os.path.exists(marker):\n"
        "    time.sleep(0.02)\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "time.sleep(120)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def _kill_pid(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    try:
        psutil.Process(pid).wait(timeout=3)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
        pass


def _wait_pids_gone(pids: list[int], timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(_pid_alive(pid) for pid in pids):
            return True
        time.sleep(0.2)
    return not any(_pid_alive(pid) for pid in pids)


def _wait_for_job_member(job_handle: int, excluded_pid: int, timeout: float = 15.0) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        members = enumerate_job_processes(job_handle)
        others = [pid for pid in members if pid != excluded_pid]
        if others:
            return others[0]
        time.sleep(0.2)
    return None


def _terminate_process(pid: int) -> bool:
    """通过 TerminateProcess 终止指定进程（PROCESS_TERMINATE）。"""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(ctypes.c_void_p(handle), 1))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def test_job_adapter_is_explicitly_degraded_off_windows():
    job = WindowsJobObject(JobLimits(max_processes=2, memory_bytes=64 * 1024 * 1024, cpu_time_100ns=10_000_000))
    job.create()
    if os.name != "nt":
        assert job.available is False
        assert job.degraded_reason == "windows_only"
        assert job.enforced_limits == {}
    else:
        assert job.enforced_limits["kill_on_close"] is True
        assert job.enforced_limits["max_processes"] == 2
        assert job.enforced_limits["memory_bytes"] == 64 * 1024 * 1024
        assert job.enforced_limits["cpu_time_100ns"] == 10_000_000
    job.close()


@pytest.mark.skipif(not _windows_job_available(), reason="Windows Job Object API 不可用")
def test_job_kill_on_close_terminates_child_tree(tmp_path):
    """关闭 Job 句柄（KILL_ON_JOB_CLOSE）后，整棵进程树（含孙进程）被终止。"""
    marker = tmp_path / "go.marker"
    job = WindowsJobObject(JobLimits())
    job.create()
    spawned_pids: list[int] = []
    try:
        parent = _spawn_parent_with_grandchild(marker)
        spawned_pids.append(parent.pid)
        job.assign(parent.pid)
        assert parent.pid in enumerate_job_processes(job.handle)
        marker.touch()  # 父进程随即 spawn 孙进程，自动进入同一个 Job
        grandchild = _wait_for_job_member(job.handle, parent.pid, timeout=15)
        assert grandchild is not None, "孙进程未进入 Job（进程树未受控）"
        spawned_pids.append(grandchild)
        assert _pid_alive(parent.pid) and _pid_alive(grandchild)
        job.close()  # KILL_ON_JOB_CLOSE → 整棵进程树终止
        assert _wait_pids_gone(spawned_pids, timeout=20), "关闭 Job 后进程树未全部终止"
        assert not _pid_alive(parent.pid)
        assert not _pid_alive(grandchild)
    finally:
        for pid in spawned_pids:
            _kill_pid(pid)
        job.close()


@pytest.mark.skipif(not _windows_job_available(), reason="Windows Job Object API 不可用")
def test_job_active_process_limit_blocks_third_process():
    """ActiveProcessLimit=2 时第 3 个并发子进程被拒绝进入 Job。"""
    job = WindowsJobObject(JobLimits(max_processes=2))
    job.create()
    pids: list[int] = []
    try:
        procs = [_spawn_sleep(30) for _ in range(3)]
        pids = [proc.pid for proc in procs]
        blocked_pid: int | None = None
        for proc in procs:
            try:
                job.assign(proc.pid)
            except JobObjectError:
                blocked_pid = proc.pid
                break
        assert blocked_pid is not None, "第 3 个进程应被 ActiveProcessLimit=2 拒绝进入 Job"
        members = enumerate_job_processes(job.handle)
        assert blocked_pid not in members
        assert len(members) <= 2
    finally:
        for pid in pids:
            _kill_pid(pid)
        job.close()


@pytest.mark.skipif(not _windows_job_available(), reason="Windows Job Object API 不可用")
def test_job_reclaimable_after_terminating_child():
    """TerminateProcess 任意子进程后，Job 整体可枚举、可回收（无残留）。"""
    job = WindowsJobObject(JobLimits())
    job.create()
    pids: list[int] = []
    try:
        procs = [_spawn_sleep(60) for _ in range(2)]
        pids = [proc.pid for proc in procs]
        for proc in procs:
            job.assign(proc.pid)
        assert _terminate_process(procs[0].pid)
        assert _wait_pids_gone([procs[0].pid], timeout=15)
        remaining = enumerate_job_processes(job.handle)
        assert procs[1].pid in remaining
        killed = kill_job(job.handle)
        assert procs[1].pid in killed
        assert _wait_pids_gone([procs[1].pid], timeout=15)
    finally:
        for pid in pids:
            _kill_pid(pid)
        job.close()
