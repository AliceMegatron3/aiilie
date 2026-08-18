"""Resource profile admission and automatic selection tests."""
from __future__ import annotations

import os
from collections import namedtuple

import pytest

from services.resource_profile import ResourceProfileError, ResourceProfileManager


Disk = namedtuple("Disk", "total used free")
Memory = namedtuple("Memory", "total available")


def test_auto_profile_prefers_high_when_capacity_available(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    monkeypatch.setattr("services.resource_profile.psutil.virtual_memory", lambda: Memory(32 * 1024 ** 3, 28 * 1024 ** 3))
    monkeypatch.setattr("services.resource_profile.shutil.disk_usage", lambda _: Disk(100 * 1024 ** 3, 0, 90 * 1024 ** 3))
    manager = ResourceProfileManager(tmp_path)
    assert manager.resolve("auto").name == "high"


def test_standard_profile_reservation_rejects_insufficient_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    monkeypatch.setattr("services.resource_profile.psutil.virtual_memory", lambda: Memory(12 * 1024 ** 3, 10 * 1024 ** 3))
    monkeypatch.setattr("services.resource_profile.shutil.disk_usage", lambda _: Disk(100 * 1024 ** 3, 0, 90 * 1024 ** 3))
    manager = ResourceProfileManager(tmp_path)
    with pytest.raises(ResourceProfileError, match="内存"):
        manager.reserve("task-1", manager.resolve("standard"))


# ── 磁盘硬配额（DiskQuotaGuard）──
from services.resource_profile import DiskQuotaExceededError, DiskQuotaGuard


def test_disk_quota_guard_measures_and_enforces(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "a.py").write_text("x" * 1000)
    guard = DiskQuotaGuard(workdir, quota_bytes=500)
    assert guard.enforced is True
    assert guard.used_bytes() >= 1000
    with pytest.raises(DiskQuotaExceededError):
        guard.check()
    assert guard.max_observed_bytes >= 1000


def test_disk_quota_guard_zero_quota_is_noop(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "a.txt").write_text("hello")
    guard = DiskQuotaGuard(workdir, quota_bytes=0)
    assert guard.enforced is False
    assert guard.check() > 0  # 不抛异常
    manifest = guard.cleanup_excess()
    assert manifest["deleted"] == []
    assert manifest["still_over"] is False


def test_disk_quota_guard_cleanup_removes_temp_artifacts(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "a.py").write_text("x" * 1000)
    pycache = workdir / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.cpython-311.pyc").write_bytes(b"z" * 2000)
    (workdir / "junk.tmp").write_bytes(b"q" * 1000)
    guard = DiskQuotaGuard(workdir, quota_bytes=2500)
    manifest = guard.cleanup_excess()
    assert manifest["freed_bytes"] >= 2000
    assert any("__pycache__" in item for item in manifest["deleted"])
    assert guard.used_bytes() <= 2500
    assert manifest["still_over"] is False
