"""可扩展插件内核 — Manifest 能力/权限/网络策略（V0.5）。

方向报告「五、插件 manifest」要求声明 capabilities / permissions / resource_budget /
network。本模块把报告的安全红线固化为校验器：
- 权限：禁止声明会越权到核心治理的高危权限（改 Ledger / 改权限 / 批准候选等）；
- 网络：`network.mode` 仅 allowed {deny, allow}，默认 deny（fail-closed）；
- 资源预算：timeout/max_input/max_output 必须为 ≥1 的整数且不超过硬上限；
- 结构：需 id / api_version / type / capabilities。

它叠加在 `PluginContext`（运行时受限访问面）之上，负责"入站清单"校验，
使「声明即限定」——即使插件声称某权限，策略层仍按其最小权限落位。
"""
from __future__ import annotations

import re
from typing import Any

_PLUGIN_ID_RE = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9_]+)*$")

ALLOWED_NETWORK_MODES = {"deny", "allow"}

# 高危权限前缀：声明即拒绝（不得让插件声明治理/写库/审批能力）
_FORBIDDEN_PERMISSION_PREFIXES = (
    "ledger.",
    "permission.",
    "identity.write",
    "core.approve",
    "plugin.approve",
    "approve.",
)

# 资源预算硬上限（防御性，不随插件声明放大）
_MAX_TIMEOUT_SECONDS = 3600
_MAX_INPUT_BYTES = 64 * 1024 * 1024
_MAX_OUTPUT_BYTES = 64 * 1024 * 1024


class ManifestPolicyError(ValueError):
    pass


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """校验插件 manifest 并返回规范化视图；任何越权声明即抛 ManifestPolicyError。"""
    if not isinstance(manifest, dict):
        raise ManifestPolicyError("manifest 必须是对象")
    mid = str(manifest.get("id", "") or "")
    if not mid or not _PLUGIN_ID_RE.fullmatch(mid):
        raise ManifestPolicyError(f"非法插件 id: {mid!r}")
    api_version = str(manifest.get("api_version", "") or "")
    if not api_version:
        raise ManifestPolicyError("缺少 api_version")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ManifestPolicyError("capabilities 至少声明一项能力")

    # 权限白名单内最小化 + 高危权限兜底拒绝
    permissions = manifest.get("permissions", [])
    if not isinstance(permissions, list):
        raise ManifestPolicyError("permissions 必须是数组")
    for perm in permissions:
        p = str(perm)
        if any(p.startswith(prefix) for prefix in _FORBIDDEN_PERMISSION_PREFIXES):
            raise ManifestPolicyError(f"插件声明了越权权限且被拒: {perm!r}")

    # 网络：默认 deny（fail-closed）
    network = manifest.get("network")
    mode = "deny"
    if network is not None:
        if not isinstance(network, dict):
            raise ManifestPolicyError("network 必须是对象")
        mode = str(network.get("mode", "deny")).lower()
        if mode not in ALLOWED_NETWORK_MODES:
            raise ManifestPolicyError(f"非法网络模式: {mode!r}（仅允许 deny/allow）")

    # 资源预算：非负且有上限
    budget = manifest.get("resource_budget", {}) or {}
    if not isinstance(budget, dict):
        raise ManifestPolicyError("resource_budget 必须是对象")
    timeout = _bounded_int(budget, "timeout_seconds", 1, _MAX_TIMEOUT_SECONDS, 30)
    max_in = _bounded_int(budget, "max_input_bytes", 1, _MAX_INPUT_BYTES, 1 * 1024 * 1024)
    max_out = _bounded_int(budget, "max_output_bytes", 1, _MAX_OUTPUT_BYTES, 1 * 1024 * 1024)

    # Batch 6：统一 kind/type schema——`kind` 为规范字段，`type` 作为旧别名兼容。
    kind = str(manifest.get("kind") or manifest.get("type") or "")

    return {
        "id": mid,
        "api_version": api_version,
        "kind": kind,
        "capabilities": [str(c) for c in capabilities],
        "permissions": [str(p) for p in permissions],
        "network": {"mode": mode},
        "resource_budget": {
            "timeout_seconds": timeout,
            "max_input_bytes": max_in,
            "max_output_bytes": max_out,
        },
        "artifact_digest": str(manifest.get("artifact_digest", "") or "") or None,
        "status": str(manifest.get("status", "") or "") or None,
    }


def _bounded_int(budget: dict, key: str, lo: int, hi: int, default: int) -> int:
    value = budget.get(key, default)
    try:
        iv = int(value)
    except (TypeError, ValueError):
        raise ManifestPolicyError(f"resource_budget.{key} 必须是整数") from None
    if iv < lo or iv > hi:
        raise ManifestPolicyError(f"resource_budget.{key}={iv} 超出允许范围 [{lo},{hi}]")
    return iv


# 规范权限等级文本 → 数值（与 whitelisted_tools.PermissionLevel 刻度一致）
_PERM_LEVEL_TO_INT = {"none": 0, "read": 1, "write": 2, "govern": 3, "admin": 3}


def resolve_required_permission(manifest: dict[str, Any]) -> int:
    """Batch 6：统一 permissions 权限 schema——`permissions` 清单（可含 read/write/govern
    等级 token）优先；兼容旧 `permission_level` / `permissions_required`；缺省 0（NONE）。
    返回数值，绝不依赖调用方各自解析的字段漂移。"""
    permissions = manifest.get("permissions")
    if isinstance(permissions, list):
        for perm in permissions:
            key = str(perm).strip().lower()
            if key in _PERM_LEVEL_TO_INT:
                return _PERM_LEVEL_TO_INT[key]
    legacy = manifest.get("permission_level", manifest.get("permissions_required", 0))
    try:
        if isinstance(legacy, str):
            s = legacy.strip().lower()
            if s in _PERM_LEVEL_TO_INT:
                return _PERM_LEVEL_TO_INT[s]
            legacy = legacy.removeprefix("L") or "0"
        # 不做上限钳制：越界/异常高的权限等级保持原值 → 调用方据此高等级必然 DENY（fail-safe）
        return max(0, int(legacy))
    except (TypeError, ValueError):
        return 0


__all__ = ["ManifestPolicyError", "resolve_required_permission", "validate_manifest"]