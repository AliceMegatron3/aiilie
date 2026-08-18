"""OS 级网络隔离策略测试（默认 off / local_only 施加与降级 / 真实 netsh 受保护）。"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

import pytest

from services.network_isolation import apply_isolation, remove_isolation


def test_default_policy_off_is_noop():
    result = apply_isolation(os.path.abspath(sys.executable), "off")
    assert result["enforced"] is False
    assert result["rule_names"] == []
    assert result["policy"] == "off"
    assert result["degraded_reason"] == ""


def test_unknown_policy_is_noop():
    result = apply_isolation(os.path.abspath(sys.executable), "banana")
    assert result["enforced"] is False
    assert result["degraded_reason"] == "unknown_policy"


def test_local_only_apply_and_remove_with_mocked_netsh(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("services.network_isolation.os.name", "nt")
    monkeypatch.setattr("services.network_isolation.subprocess.run", fake_run)
    result = apply_isolation("C:/Python312/python.exe", "local_only")
    assert result["enforced"] is True
    assert len(result["rule_names"]) == 1
    add_calls = [cmd for cmd in calls if "add" in cmd]
    assert add_calls, "应调用 netsh add rule"
    assert any("remoteip=localsubnet" in cmd for cmd in add_calls)
    assert any("dir=out" in cmd and "action=block" in cmd for cmd in add_calls)
    # 幂等：施加前先删除同名规则
    assert any("delete" in cmd for cmd in calls)
    removed = remove_isolation(result["rule_names"])
    assert removed["removed"] == result["rule_names"]


def test_full_policy_uses_remoteip_any(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("services.network_isolation.os.name", "nt")
    monkeypatch.setattr("services.network_isolation.subprocess.run", fake_run)
    result = apply_isolation("C:/Python312/python.exe", "full")
    assert result["enforced"] is True
    add_calls = [cmd for cmd in calls if "add" in cmd]
    assert any("remoteip=any" in cmd for cmd in add_calls)


def test_apply_degrades_when_netsh_fails(monkeypatch):
    def failing_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr("services.network_isolation.os.name", "nt")
    monkeypatch.setattr("services.network_isolation.subprocess.run", failing_run)
    result = apply_isolation("C:/Python312/python.exe", "full")
    assert result["enforced"] is False
    assert "netsh" in result["degraded_reason"]


def test_apply_degrades_off_windows(monkeypatch):
    monkeypatch.setattr("services.network_isolation.os.name", "posix")
    result = apply_isolation("/usr/bin/python3", "full")
    assert result["enforced"] is False
    assert result["degraded_reason"] == "windows_only"


def test_remove_isolation_is_idempotent(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("services.network_isolation.os.name", "nt")
    monkeypatch.setattr("services.network_isolation.subprocess.run", fake_run)
    # 重复删除同一规则不应失败（不存在即视为已清理）
    first = remove_isolation(["rule_a"])
    second = remove_isolation(["rule_a"])
    assert first["removed"] == ["rule_a"]
    assert second["removed"] == ["rule_a"]


def _can_run_netsh() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


@pytest.mark.skipif(not _can_run_netsh(), reason="需要 Windows 管理员权限运行真实 netsh")
def test_netsh_apply_and_remove_real_when_admin():
    """真实 netsh 施加/删除规则（本地 Windows 管理员可跑，CI/沙箱跳过）。"""
    rule_name = ""
    try:
        result = apply_isolation(os.path.abspath(sys.executable), "local_only")
        assert result["enforced"] is True, result["degraded_reason"]
        rule_name = result["rule_names"][0]
        probe = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
            capture_output=True, text=True, timeout=30,
        )
        assert rule_name in probe.stdout
    finally:
        if rule_name:
            remove_isolation([rule_name])
            probe = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
                capture_output=True, text=True, timeout=30,
            )
            assert rule_name not in probe.stdout
