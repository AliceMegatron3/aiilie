"""插件健康检查测试（V0.5：确定性体检，fail-closed）。"""
from __future__ import annotations

import pytest

from services.plugin_context import PluginContext, ResourceBudget
from services.plugin_health import check_plugin_health


def _manifest(**over):
    m = {
        "id": "quantifier.evidence.v1",
        "api_version": "1",
        "type": "quantifier",
        "capabilities": ["document.read"],
        "permissions": ["document.read"],
        "resource_budget": {"timeout_seconds": 30, "max_input_bytes": 1048576, "max_output_bytes": 1048576},
        "network": {"mode": "deny"},
    }
    m.update(over)
    return m


async def _run_check(manifest, ctx):
    return check_plugin_health(manifest, ctx)


def test_health_check_all_sane_and_surface_clean():
    ctx = PluginContext(identity="plug-x", project_scope="proj_a")
    report = check_plugin_health(_manifest(), ctx)
    assert report.healthy is True
    assert report.status == "HEALTHY"


def test_manifest_policy_failure_marks_degraded():
    ctx = PluginContext(identity="plug-x", project_scope="proj_a")
    report = check_plugin_health(_manifest(permissions=["ledger.write"]), ctx)
    assert report.healthy is False
    assert report.status == "DEGRADED"
    assert any(c.name == "manifest_policy" and not c.passed for c in report.checks)


def test_unknown_capability_marks_degraded():
    ctx = PluginContext(identity="plug-x", project_scope="proj_a")
    report = check_plugin_health(_manifest(capabilities=["nonsense.cap"]), ctx)
    assert report.healthy is False
    assert any(c.name == "capabilities_supported" and not c.passed for c in report.checks)


def test_missing_budget_marks_degraded():
    # 上下文无合理预算（超时=0）→ budget_sane 失败 → DEGRADED
    bad_ctx = PluginContext(identity="plug-x", project_scope="proj_a",
                            budget=ResourceBudget(timeout_seconds=0, max_input_bytes=1, max_output_bytes=1))
    report = check_plugin_health(_manifest(), bad_ctx)
    assert report.healthy is False
    assert any(c.name == "budget_sane" and not c.passed for c in report.checks)