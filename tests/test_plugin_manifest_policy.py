"""Manifest 能力/权限/网络策略测试（V0.5 插件内核）。"""
from __future__ import annotations

import pytest

from services.plugin_manifest_policy import ManifestPolicyError, resolve_required_permission, validate_manifest


def _valid():
    return {
        "id": "quantifier.evidence.v1",
        "api_version": "1",
        "type": "quantifier",
        "capabilities": ["knowledge.evidence_score"],
        "permissions": ["document.read"],
        "resource_budget": {"timeout_seconds": 30, "max_input_bytes": 1048576, "max_output_bytes": 1048576},
        "network": {"mode": "deny"},
        "status": "experimental",
    }


def test_valid_manifest_normalized():
    out = validate_manifest(_valid())
    assert out["id"] == "quantifier.evidence.v1"
    assert out["network"] == {"mode": "deny"}
    assert out["resource_budget"]["timeout_seconds"] == 30


def test_missing_id_or_api_version_rejected():
    with pytest.raises(ManifestPolicyError):
        validate_manifest({**_valid(), "id": ""})
    with pytest.raises(ManifestPolicyError):
        validate_manifest({**_valid(), "api_version": ""})


def test_empty_capabilities_rejected():
    with pytest.raises(ManifestPolicyError):
        validate_manifest({**_valid(), "capabilities": []})


def test_forbidden_privileged_permission_rejected():
    for bad in ("ledger.write", "core.approve", "permission.write", "approve.skill"):
        with pytest.raises(ManifestPolicyError):
            validate_manifest({**_valid(), "permissions": [bad]})


def test_network_defaults_to_deny_and_rejects_unknown():
    out = validate_manifest({**_valid()})  # 未声明 network → deny
    assert out["network"] == {"mode": "deny"}
    with pytest.raises(ManifestPolicyError):
        validate_manifest({**_valid(), "network": {"mode": "public"}})


def test_resource_budget_out_of_range_rejected():
    with pytest.raises(ManifestPolicyError):
        validate_manifest({**_valid(), "resource_budget": {"timeout_seconds": 0, "max_input_bytes": 1, "max_output_bytes": 1}})
    with pytest.raises(ManifestPolicyError):
        validate_manifest({**_valid(), "resource_budget": {"timeout_seconds": 999999, "max_input_bytes": 1, "max_output_bytes": 1}})


def test_kind_type_schema_normalized_to_kind():
    """Batch 6：`kind` 为规范字段，`type` 作为旧别名；二者皆不声明则 kind 为空。"""
    out = validate_manifest({**_valid(), "type": "quantifier"})
    assert out["kind"] == "quantifier"  # 旧 type 兼容映射到 kind
    out2 = validate_manifest({**_valid(), "kind": "metric", "type": "quantifier"})
    assert out2["kind"] == "metric"      # kind 优先于 type
    out3 = validate_manifest({**_valid(), "kind": ""})
    assert out3["kind"] == "quantifier"  # kind 空 → 回落到旧 type


def test_permissions_schema_normalized_to_numeric_level():
    """Batch 6：permissions schema 漂移——`permissions` 的 read/write/govern token 优先，
    兼容旧 permission_level / permissions_required，缺省 0(NONE)。"""
    assert resolve_required_permission({"permissions": ["document.read", "govern"]}) == 3
    assert resolve_required_permission({"permissions": ["read"]}) == 1
    # 旧字段兼容
    assert resolve_required_permission({"permission_level": 2}) == 2
    assert resolve_required_permission({"permissions_required": "L3"}) == 3
    # 缺省 → NONE(0)；非法 → 0（fail-closed 的低权限）
    assert resolve_required_permission({}) == 0
    assert resolve_required_permission({"permission_level": "not-an-int"}) == 0