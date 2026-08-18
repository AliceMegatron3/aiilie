"""计划执行器审计终态回流测试（V0.4：每步 OK/ERROR 回流到可回放审计轨）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.plan_audit import PlanAuditStore
from services.plan_executor import PlanExecutor


@pytest.mark.asyncio
async def test_audit_terminal_reflows_per_step(tmp_path):
    audit = PlanAuditStore(base_dir=tmp_path)
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="检索+未知工具")
    v1 = plan.draft(
        "检索",
        [
            PlanStep(tool="library.search_cards", args={"query": "x"}),
            PlanStep(tool="evil.tool", args={}),
        ],
    )
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    async def search(args):
        return {"hits": []}

    executor = PlanExecutor(audit_store=audit)
    results = await executor.execute_plan(
        plan, version=v1, actor="author",
        handlers={"library.search_cards": search},
    )
    # 可执行工具 OK + 未知工具 DENIED
    assert results[0].status == "OK"
    assert results[1].status == "DENIED"
    # 审计轨含每步终态
    events = await audit.list(plan.plan_id)
    assert len(events) == 2
    by_tool = {e["tool"]: e for e in events}
    assert by_tool["library.search_cards"]["status"] == "OK"
    assert by_tool["evil.tool"]["status"] == "DENIED"
    assert all(e["action"] == "completed" for e in events)