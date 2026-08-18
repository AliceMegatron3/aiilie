"""受控执行 — 计划风险门（V0.4：risk_level 生效 + 用户/项目权限校验）。

方向报告「四、计划模式」的风险对象含 `risk_level`；「每个工具必须经过」含
"用户/项目权限校验"。本模块把 plan.risk_level 映射为允许的最大工具权限档位：
- low   → READ
- medium→ WRITE
- high  → GOVERN

`assert_plan_within_risk(...)` 校验：任一计划步骤所用工具若要求权限 > 计划风险允许档，
即视为越权（fail-closed）。这样"风险等级"与"工具权限"在执行门真正联动——
高风险计划可执行 GOVERN 工具，低风险计划不得调用写/治理类工具。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from services.whitelisted_tools import PermissionLevel, WhitelistedToolRegistry

if TYPE_CHECKING:
    from services.agent_plan import AgentPlan


class PlanRiskError(RuntimeError):
    pass


RISK_ORDER = {"low": 1, "medium": 2, "high": 3}


def risk_to_permission(risk: str) -> PermissionLevel:
    risk = (risk or "low").strip().lower()
    if risk not in RISK_ORDER:
        raise PlanRiskError(f"非法 risk_level: {risk!r}（应 low/medium/high）")
    return {
        "low": PermissionLevel.READ,
        "medium": PermissionLevel.WRITE,
        "high": PermissionLevel.GOVERN,
    }[risk]


def _current_snapshot(plan: "AgentPlan"):
    """取计划当前（最新）版本快照；Batch 5：修复对不存在 `steps_current_version`
    属性的错误访问，改为经 snapshot(latest_version) 读取冻结步骤与 risk_level。"""
    latest = getattr(plan, "latest_version", 0)
    return plan.snapshot(latest)


def out_of_risk_tools(
    plan: "AgentPlan",
    registry: WhitelistedToolRegistry,
    permission_cap: PermissionLevel,
) -> list[tuple[str, PermissionLevel]]:
    """返回需要权限高于 `permission_cap` 的计划工具列表（执行前越权快检）。"""
    bad: list[tuple[str, PermissionLevel]] = []
    for step in _current_snapshot(plan).steps:
        spec = registry.spec(step.tool)
        if spec is not None and spec.required_permission > permission_cap:
            bad.append((step.tool, spec.required_permission))
    return bad


def assert_plan_within_risk(
    plan: "AgentPlan",
    registry: WhitelistedToolRegistry,
    permission_cap: PermissionLevel,
) -> None:
    bad = out_of_risk_tools(plan, registry, permission_cap)
    if bad:
        names = ", ".join(f"{t}→{p.name}" for t, p in bad)
        raise PlanRiskError(f"计划步骤引用超出风险档({permission_cap.name})的工具：{names}")


__all__ = ["PlanRiskError", "RISK_ORDER", "risk_to_permission", "out_of_risk_tools",
           "assert_plan_within_risk"]