"""OS 级网络隔离策略（Windows netsh advfirewall 临时出站规则）。

配置键 ``code_execution.network_policy``，默认 ``off``（保持现状）：
- ``off``:        不施加任何限制（现状）。
- ``local_only``: 阻断对本地子网的出站（``remoteip=localsubnet``）。
- ``full``:       阻断全部出站（``remoteip=any``）。

实现要点：
- 规则按 policy+程序路径哈希+随机 nonce 命名，任务内唯一，可并发隔离不同程序；
- 施加前先删除同名残留规则（幂等），任务结束由 ``remove_isolation`` 清理；
- ``netsh`` 需要管理员权限；非 Windows、权限不足或调用失败时降级为
  ``enforced=False`` 并返回 ``degraded_reason``，调用方在任务报告中标记
  ``network_isolation: "not_enforced"``。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from typing import Any

_NETSH = "netsh"
_RULE_PREFIX = "aiilie_codetask_net"
_SUPPORTED_POLICIES = ("off", "local_only", "full")


def _rule_names(program: str, policy: str) -> list[str]:
    digest = hashlib.sha256(os.path.abspath(program).encode()).hexdigest()[:10]
    nonce = uuid.uuid4().hex[:8]
    return [f"{_RULE_PREFIX}_{policy}_{digest}_{nonce}"]


def apply_isolation(profile_exe: str, policy: str) -> dict[str, Any]:
    """对指定可执行文件施加出站网络隔离，返回隔离结果描述。

    返回结构：{policy, program, enforced, rule_names, degraded_reason}。
    ``enforced=False`` 表示未能施加 OS 级规则（策略为 off / 非 Windows /
    netsh 失败 / 无管理员权限）。
    """
    # YAML 可能把 "off" 解析为布尔 False；统一字符串化并归一为 off。
    if not policy:
        policy = "off"
    else:
        policy = str(policy).strip().lower()
    result: dict[str, Any] = {
        "policy": policy,
        "program": os.path.abspath(profile_exe),
        "enforced": False,
        "rule_names": [],
        "degraded_reason": "",
    }
    if policy not in _SUPPORTED_POLICIES or policy == "off":
        result["degraded_reason"] = "" if policy == "off" else "unknown_policy"
        return result
    if os.name != "nt":
        result["degraded_reason"] = "windows_only"
        return result
    remote_ip = "localsubnet" if policy == "local_only" else "any"
    rule_names = _rule_names(profile_exe, policy)
    try:
        for rule in rule_names:
            # 幂等：先清理同名残留规则再添加
            subprocess.run(
                [_NETSH, "advfirewall", "firewall", "delete", "rule", f"name={rule}"],
                capture_output=True, timeout=15, check=False,
            )
            subprocess.run(
                [
                    _NETSH, "advfirewall", "firewall", "add", "rule",
                    f"name={rule}", "dir=out", "action=block",
                    f"program={result['program']}", f"remoteip={remote_ip}",
                    "profile=any",
                ],
                capture_output=True, timeout=30, check=True,
            )
        result["enforced"] = True
        result["rule_names"] = rule_names
    except (subprocess.SubprocessError, OSError) as exc:
        result["degraded_reason"] = f"netsh_failed: {exc}"
        # 即使部分添加成功也返回规则名，便于调用方清理
        result["rule_names"] = rule_names
    return result


def remove_isolation(rule_names: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """幂等删除指定隔离规则（删除不存在的规则不视为失败）。

    返回 {removed: [...], failed: [...]}。
    """
    result: dict[str, Any] = {"removed": [], "failed": []}
    if os.name != "nt" or not rule_names:
        return result
    for rule in rule_names:
        try:
            subprocess.run(
                [_NETSH, "advfirewall", "firewall", "delete", "rule", f"name={rule}"],
                capture_output=True, timeout=15, check=False,
            )
            result["removed"].append(rule)
        except (subprocess.SubprocessError, OSError):
            result["failed"].append(rule)
    return result
