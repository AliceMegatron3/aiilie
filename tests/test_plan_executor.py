"""计划执行器测试（V0.4：批准门 + 白名单 + 真实 handler 编排）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanGateError, PlanStep
from services.plan_executor import PlanExecutor
from services.whitelisted_tools import PermissionLevel


def _approved_plan(identity: str, steps: list[PlanStep], goal: str = "量化书籍"):
    plan = AgentPlan(workspace_id="ws", identity=identity, goal=goal)
    v1 = plan.draft(goal, steps)
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")
    return plan, v1


@pytest.mark.asyncio
async def test_executes_approved_steps_via_wired_handlers():
    plan, v1 = _approved_plan("agent-a", [PlanStep(tool="project.query", args={"id": "p1"})])

    async def query(args):
        return {"project_id": args["id"]}

    executor = PlanExecutor()
    results = await executor.execute_plan(plan, version=v1, actor="author", handlers={"project.query": query})
    assert len(results) == 1
    assert results[0].status == "OK"
    assert results[0].result == {"project_id": "p1"}


@pytest.mark.asyncio
async def test_unapproved_plan_propagates_gate_error():
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="x")
    v1 = plan.draft("x", [PlanStep(tool="project.query", args={"id": "p1"})])  # 未批准

    async def query(args):
        return {}

    executor = PlanExecutor()
    with pytest.raises(PlanGateError):
        await executor.execute_plan(plan, version=v1, actor="author", handlers={"project.query": query})


@pytest.mark.asyncio
async def test_unwired_handler_is_denied_not_executed():
    plan, v1 = _approved_plan("agent-a", [PlanStep(tool="project.query", args={"id": "p1"})])
    executor = PlanExecutor()
    results = await executor.execute_plan(plan, version=v1, actor="author", handlers={})
    assert results[0].status == "DENIED"


@pytest.mark.asyncio
async def test_unknown_tool_denied_by_whitelist():
    plan, v1 = _approved_plan("agent-a", [PlanStep(tool="evil.shell", args={})])

    async def shell(args):
        return "should not run"

    executor = PlanExecutor()
    results = await executor.execute_plan(plan, version=v1, actor="author", handlers={"evil.shell": shell})
    assert results[0].status == "DENIED"


@pytest.mark.asyncio
async def test_multi_step_returns_in_order_with_audit():
    plan, v1 = _approved_plan(
        "agent-a",
        [
            PlanStep(tool="project.query", args={"id": "p1"}),
            PlanStep(tool="document.read", args={"doc_id": "d1"}),
        ],
    )

    async def query(args):
        return "q"

    async def read(args):
        return [{"doc": args["doc_id"]}]

    executor = PlanExecutor()
    results = await executor.execute_plan(
        plan, version=v1, actor="author", handlers={"project.query": query, "document.read": read}
    )
    assert [r.tool for r in results] == ["project.query", "document.read"]
    assert all(r.status == "OK" for r in results)