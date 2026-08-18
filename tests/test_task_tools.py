"""任务域 tool handler 测试（V0.4：task.quantize_progress 真实进度查询）。

覆盖：
- 注入 task_manager → 返回真实 get_task_detail 的 status/progress；
- 任务不存在 → 抛错（不伪造"已完成"）；
- 未注入 task_manager → 结构化 DISABLED（fail-closed）；
- 缺少 task_id → 抛错；
- 已批准计划内执行 task.quantize_progress。
"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.plan_executor import PlanExecutor
from services.plan_tools_root import build_default_handlers
from services.task_tools import make_quantize_progress_handler


class FakeTaskManager:
    """TaskManager.get_task_detail 的最小替身。"""

    def __init__(self, detail):
        self._detail = detail

    async def get_task_detail(self, task_id: str):
        return self._detail


@pytest.mark.asyncio
async def test_quantize_progress_returns_detail():
    tm = FakeTaskManager({
        "task_id": "q1", "status": "RUNNING",
        "progress": {"total": 10, "completed": 4, "percent": 40.0},
    })
    res = await make_quantize_progress_handler(tm)({"task_id": "q1"})
    assert res["status"] == "RUNNING"
    assert res["progress"]["percent"] == 40.0
    assert res["task_id"] == "q1"


@pytest.mark.asyncio
async def test_quantize_progress_missing_task_raises():
    handler = make_quantize_progress_handler(FakeTaskManager(None))
    with pytest.raises(ValueError, match="不存在"):
        await handler({"task_id": "nope"})


@pytest.mark.asyncio
async def test_quantize_progress_unwired_disabled():
    res = await make_quantize_progress_handler(None)({"task_id": "q1"})
    assert res["status"] == "DISABLED"
    assert res["feature"] == "task.quantize_progress"
    assert res["reason"] == "task_manager_not_wired"


@pytest.mark.asyncio
async def test_quantize_progress_requires_task_id():
    handler = make_quantize_progress_handler(FakeTaskManager({}))
    with pytest.raises(ValueError, match="task_id"):
        await handler({})


@pytest.mark.asyncio
async def test_quantize_progress_within_approved_plan():
    tm = FakeTaskManager({
        "task_id": "q1", "status": "COMPLETED",
        "progress": {"total": 5, "completed": 5, "percent": 100.0},
    })
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="查量化进度")
    v1 = plan.draft("查量化进度", [
        PlanStep(tool="task.quantize_progress", args={"task_id": "q1"}),
    ])
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")
    results = await PlanExecutor().execute_plan(
        plan, version=v1, actor="author",
        handlers=build_default_handlers(task_manager=tm),
    )
    assert results[0].status == "OK"
    assert results[0].result["progress"]["percent"] == 100.0
