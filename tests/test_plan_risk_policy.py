"""Batch 5：计划风险门——out_of_risk_tools/assert_plan_within_risk 经当前版本快照
读取步骤与 risk_level（修复对不存在 steps_current_version 的访问），低风险计划不得
调用写/治理类工具（fail-closed）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.plan_risk_policy import (
    PlanRiskError,
    assert_plan_within_risk,
    out_of_risk_tools,
    risk_to_permission,
)
from services.whitelisted_tools import PermissionLevel, get_report_registry


def _plan(steps: list[PlanStep], risk_level: str = "low") -> AgentPlan:
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="x")
    plan.draft("x", steps, risk_level=risk_level)
    return plan


def test_risk_to_permission_mapping():
    assert risk_to_permission("low") == PermissionLevel.READ
    assert risk_to_permission("medium") == PermissionLevel.WRITE
    assert risk_to_permission("high") == PermissionLevel.GOVERN
    with pytest.raises(PlanRiskError):
        risk_to_permission("critical")


def test_out_of_risk_reads_current_snapshot_not_broken_attr():
    # 只读工具（READ）在 low（cap=READ）下不越权
    plan = _plan([PlanStep(tool="project.query", args={"id": "p1"})], risk_level="low")
    bad = out_of_risk_tools(plan, get_report_registry(), PermissionLevel.READ)
    assert bad == []

    # 写工具（WRITE）在 low（cap=READ）下越权
    plan2 = _plan([PlanStep(tool="document.draft_create", args={"project_id": "p", "name": "d"})], risk_level="low")
    bad2 = out_of_risk_tools(plan2, get_report_registry(), PermissionLevel.READ)
    assert [t for t, _ in bad2] == ["document.draft_create"]


def test_assert_plan_within_risk_fail_closed_for_write_at_low_risk():
    plan = _plan(
        [PlanStep(tool="document.draft_create", args={"project_id": "p", "name": "d"})],
        risk_level="low",
    )
    # low 计划 + READ 上限 → WRITE 工具 403 级别拒绝
    with pytest.raises(PlanRiskError, match="超出风险档"):
        assert_plan_within_risk(plan, get_report_registry(), PermissionLevel.READ)
    # 但 medium 计划 + WRITE 上限 → 放行（写工具在 medium 风险内）
    plan2 = _plan(
        [PlanStep(tool="document.draft_create", args={"project_id": "p", "name": "d"})],
        risk_level="medium",
    )
    assert_plan_within_risk(plan2, get_report_registry(), PermissionLevel.WRITE)