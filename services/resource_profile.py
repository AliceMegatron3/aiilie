"""CodeTask resource profiles, host admission, and in-process reservations."""
from __future__ import annotations

import os
import shutil
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import psutil

from core.config_manager import config_manager


class ResourceProfileError(ValueError):
    pass


class DiskQuotaExceededError(RuntimeError):
    """CodeTask 工作目录磁盘使用量超过硬配额。"""

    def __init__(self, used_bytes: int, quota_bytes: int) -> None:
        self.used_bytes = int(used_bytes)
        self.quota_bytes = int(quota_bytes)
        super().__init__(
            f"DISK_QUOTA_EXCEEDED: 已用 {self.used_bytes} 字节, 配额 {self.quota_bytes} 字节"
        )


def _path_byte_size(path: Path) -> int:
    """单个文件或目录（递归）占用的字节数，用于按回收空间排序。"""
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


class DiskQuotaGuard:
    """对 CodeTask 工作目录实施硬磁盘配额（配置档位 disk_gb 的落地执行）。

    - used_bytes(): 递归汇总工作目录内全部文件字节（临时产物一并计入）。
    - check():      超限抛 DiskQuotaExceededError；同时记录峰值供审计反馈。
    - cleanup_excess(): 删除缓存/临时产物把使用量压回配额内，
                        返回现场快照（deleted/freed_bytes/used_before/used_after）。
    """

    _CACHE_DIR_MARKERS = {"__pycache__", ".pytest_cache", ".cache", ".mypy_cache"}
    _TEMP_FILE_SUFFIXES = {".pyc", ".tmp", ".log", ".out"}

    def __init__(self, workdir: Path, quota_bytes: int) -> None:
        self.workdir = Path(workdir)
        self.quota_bytes = max(0, int(quota_bytes))
        self.max_observed_bytes = 0
        self.enforced = self.quota_bytes > 0

    def used_bytes(self) -> int:
        if not self.workdir.exists():
            return 0
        total = 0
        for path in self.workdir.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def check(self) -> int:
        used = self.used_bytes()
        if used > self.max_observed_bytes:
            self.max_observed_bytes = used
        if self.enforced and used > self.quota_bytes:
            raise DiskQuotaExceededError(used, self.quota_bytes)
        return used

    def cleanup_excess(self) -> dict[str, Any]:
        """删除缓存/临时产物，把使用量压回配额内，返回现场快照供反馈。"""
        manifest: dict[str, Any] = {
            "workdir": str(self.workdir),
            "quota_bytes": self.quota_bytes,
            "used_before": 0,
            "used_after": 0,
            "freed_bytes": 0,
            "deleted": [],
            "still_over": False,
        }
        if not self.workdir.exists():
            return manifest
        used_before = self.used_bytes()
        manifest["used_before"] = used_before
        if not self.enforced or used_before <= self.quota_bytes:
            manifest["used_after"] = used_before
            return manifest
        targets: list[Path] = []
        for path in self.workdir.rglob("*"):
            if path.is_dir() and path.name in self._CACHE_DIR_MARKERS:
                targets.append(path)
            elif path.is_file() and path.suffix.lower() in self._TEMP_FILE_SUFFIXES:
                targets.append(path)
        for target in sorted(targets, key=_path_byte_size, reverse=True):
            if manifest["used_before"] - manifest["freed_bytes"] <= self.quota_bytes:
                break
            size = _path_byte_size(target)
            try:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                continue
            manifest["freed_bytes"] += size
            manifest["deleted"].append(str(target))
        manifest["used_after"] = self.used_bytes()
        manifest["still_over"] = self.enforced and manifest["used_after"] > self.quota_bytes
        return manifest


@dataclass(frozen=True)
class ResolvedResourceProfile:
    name: str
    cpu_cores: int
    memory_mb: int
    disk_bytes: int
    max_processes: int
    timeout_seconds: int
    cpu_time_seconds: int
    enforcement: Literal["required", "best_effort"]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResourceProfileManager:
    """Conservative profile selection with resource reservations per CodeTask."""

    def __init__(self, staging_root: Path) -> None:
        self.staging_root = staging_root
        self._reservations: dict[str, ResolvedResourceProfile] = {}
        self._lock = threading.Lock()

    def available_profiles(self) -> list[str]:
        profiles = config_manager.get("code_execution.profiles", {}) or {}
        return [name for name in ("low", "standard", "high") if name in profiles]

    def resolve(self, requested: str = "auto") -> ResolvedResourceProfile:
        names = self.available_profiles()
        if not names:
            raise ResourceProfileError("未配置可用的代码执行资源档位")
        if requested not in {"auto", *names}:
            raise ResourceProfileError("未知资源档位")
        # 计划签发不预占实时资源。auto 在有余量时优先标准档，
        # 实际执行前再以 reserve() 做最终容量判定。
        if requested != "auto":
            return self._profile(requested)
        for name in ("high", "standard", "low"):
            if name not in names:
                continue
            profile = self._profile(name)
            try:
                self._validate_capacity(profile)
                return profile
            except ResourceProfileError:
                continue
        return self._profile("low")

    def reserve(self, task_id: str, profile: ResolvedResourceProfile) -> None:
        with self._lock:
            if task_id in self._reservations:
                return
            if profile.name != "low":
                self._validate_capacity(profile, lock_held=True)
            self._reservations[task_id] = profile

    def release(self, task_id: str) -> None:
        with self._lock:
            self._reservations.pop(task_id, None)

    def snapshot(self) -> dict[str, Any]:
        cpu_count = os.cpu_count() or 1
        memory = psutil.virtual_memory()
        disk = shutil.disk_usage(self.staging_root)
        with self._lock:
            reserved = list(self._reservations.values())
        return {
            "host": {
                "logical_cpu_count": cpu_count,
                "memory_available_mb": memory.available // (1024 * 1024),
                "disk_available_gb": round(disk.free / (1024 ** 3), 2),
            },
            "reserved": {
                "tasks": len(reserved),
                "cpu_cores": sum(item.cpu_cores for item in reserved),
                "memory_mb": sum(item.memory_mb for item in reserved),
                "disk_gb": round(sum(item.disk_bytes for item in reserved) / (1024 ** 3), 2),
            },
        }

    def _profile(self, name: str) -> ResolvedResourceProfile:
        raw = config_manager.get(f"code_execution.profiles.{name}", {}) or {}
        return ResolvedResourceProfile(
            name=name,
            cpu_cores=int(raw.get("cpu_cores", 6)),
            memory_mb=int(raw.get("memory_mb", 128)),
            disk_bytes=int(raw.get("disk_gb", 1)) * 1024 ** 3,
            max_processes=int(raw.get("max_processes", 8)),
            timeout_seconds=int(raw.get("timeout_seconds", 900)),
            cpu_time_seconds=int(raw.get("cpu_time_seconds", 900)),
            enforcement="required" if name == "high" else "best_effort",
        )

    def _validate_capacity(self, profile: ResolvedResourceProfile, lock_held: bool = False) -> None:
        reserve = config_manager.get("code_execution.host_reserve", {}) or {}
        reserve_cpu = int(reserve.get("cpu_cores", 2))
        reserve_memory = int(reserve.get("memory_mb", 4096))
        reserve_disk = int(reserve.get("disk_gb", 8)) * 1024 ** 3
        cpu_count = os.cpu_count() or 1
        memory = psutil.virtual_memory()
        disk = shutil.disk_usage(self.staging_root)
        if lock_held:
            reserved = list(self._reservations.values())
        else:
            with self._lock:
                reserved = list(self._reservations.values())
        reserved_cpu = sum(item.cpu_cores for item in reserved)
        reserved_memory = sum(item.memory_mb for item in reserved)
        reserved_disk = sum(item.disk_bytes for item in reserved)
        if profile.cpu_cores + reserved_cpu > max(1, cpu_count - reserve_cpu):
            raise ResourceProfileError("可用 CPU 核心不足")
        if profile.memory_mb + reserved_memory > max(0, memory.available // (1024 * 1024) - reserve_memory):
            raise ResourceProfileError("可用内存不足")
        if profile.disk_bytes + reserved_disk > max(0, disk.free - reserve_disk):
            raise ResourceProfileError("staging 所在卷可用磁盘不足")
