"""可扩展插件内核 — 插件健康检查（V0.5：插件健康检查 / 静默降级的体检）。

方向报告「V0.5 目标」含"插件健康检查"。本模块组合前面的确定构件做一个
无 LLM 的健康体检：
- manifest 是否通过能力/权限/网络/预算策略（`validate_manifest`）；
- 插件上下文是否未暴露特权面（`assert_no_privileged_surface`）；
- capabilities 是否都有可用的上下文能力方法；
- 资源预算是否在合理档位（已由策略限上界，此处再校验非负）。

`PluginHealthReport` 逐项输出，`is_healthy` 全过才算健康；任一失败即标记 DEGRADED
（fail-closed：不把"半配插件"当健康可用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.plugin_context import PluginContext, assert_no_privileged_surface
from services.plugin_manifest_policy import ManifestPolicyError, validate_manifest

# capability → 需要可用的 PluginContext 方法
_CAPABILITY_METHOD = {
    "document.read": "read_document",
    "knowledge.search": "search_resources",
    "knowledge.submit_candidate": "submit_candidate",
    "audit.emit": "emit_audit_event",
}
_CONTEXT_METHODS = {"read_document", "search_resources", "submit_candidate", "emit_audit_event"}


@dataclass
class HealthCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class PluginHealthReport:
    plugin_id: str
    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def status(self) -> str:
        return "HEALTHY" if self.healthy else "DEGRADED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "status": self.status,
            "healthy": self.healthy,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
        }


def check_plugin_health(manifest: dict[str, Any], ctx: PluginContext) -> PluginHealthReport:
    checks: list[HealthCheck] = []
    pid = str(manifest.get("id", "") or "") or ctx.identity

    try:
        norm = validate_manifest(manifest)
        checks.append(HealthCheck("manifest_policy", True, f"capabilities={norm['capabilities']}"))
    except ManifestPolicyError as exc:
        checks.append(HealthCheck("manifest_policy", False, str(exc)))

    surface = assert_no_privileged_surface(ctx)
    checks.append(HealthCheck("no_privileged_surface", not surface,
                              f"越权成员: {surface}" if surface else "未暴露权限面"))

    caps = manifest.get("capabilities") or []
    missing = [c for c in caps if _CAPABILITY_METHOD.get(c) not in _CONTEXT_METHODS]
    checks.append(HealthCheck("capabilities_supported", not missing,
                              f"无能力对应上下文方法" if missing else "capabilities 均可满足"))

    budget = (ctx.resource_budget or None)
    checks.append(
        HealthCheck(
            "budget_sane",
            budget is not None and budget.timeout_seconds >= 1 and budget.max_input_bytes >= 1 and budget.max_output_bytes >= 1,
            "资源预算缺失或不合理" if budget is None else "资源预算合理",
        )
    )
    return PluginHealthReport(plugin_id=pid, checks=checks)


def is_healthy(report: PluginHealthReport) -> bool:
    return report.healthy


__all__ = ["HealthCheck", "PluginHealthReport", "check_plugin_health", "is_healthy"]