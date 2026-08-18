"""默认 tool handler 总接线测试（V0.4：跨域工具在一个已批准计划内执行）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_metrics import CandidateKnowledge, EvidenceRef
from services.plan_executor import PlanExecutor
from services.plan_tools_root import build_default_handlers
from services.skill_bank import SkillBank


@pytest.mark.asyncio
async def test_multi_domain_plan_runs_all_default_tools(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    await store.add_candidate(CandidateKnowledge(
        claim="因果推理需可证伪证据",
        evidence=[EvidenceRef(document_id="d1", chapter="c1", quote="因果推理需可证伪证据")],
    ))
    bank = SkillBank()
    bank.get("skill.extracted", "1.0.0").propose_candidate("minor")

    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="跨域检索与一致性")
    v1 = plan.draft(
        "跨域检索与一致性",
        [
            PlanStep(tool="library.search_cards", args={"query": "因果", "limit": 5}),
            PlanStep(tool="reflection.view_candidates", args={}),
            PlanStep(tool="knowledge.consistency_check", args={}),
        ],
    )
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    results = await PlanExecutor().execute_plan(
        plan, version=v1, actor="author", handlers=build_default_handlers(store, bank)
    )
    by_tool = {r.tool: r for r in results}
    assert by_tool["library.search_cards"].status == "OK"
    assert by_tool["library.search_cards"].result["count"] >= 1
    assert by_tool["reflection.view_candidates"].status == "OK"
    assert by_tool["reflection.view_candidates"].result["candidates"]["skill.extracted"] == ["skill.extracted@1.1.0-candidate"]
    assert by_tool["knowledge.consistency_check"].status == "OK"
    assert by_tool["knowledge.consistency_check"].result["total_claims"] == 1


def test_default_registry_covers_remaining_whitelist_tools():
    """V0.4 收口：默认注册表覆盖全部白名单工具的 handler（含其余工具）。"""
    handlers = build_default_handlers()
    for tool in (
        "project.query", "document.read", "document.draft_create",
        "library.search_cards", "library.quantize_start",
        "reflection.view_candidates", "reflection.view_active", "reflection.start",
        "knowledge.consistency_check", "timeline.event_create", "task.quantize_progress",
        "author_review.submit",
    ):
        assert tool in handlers, f"默认注册表缺少 {tool}"


@pytest.mark.asyncio
async def test_extended_plan_runs_remaining_tools(tmp_path):
    """V0.4 收口：量化发起 / 时间轴事件 / 量化进度 在同一已批准计划内执行。"""
    from core.config_manager import config_manager
    from models.project import AuthorProject
    from models.timeline import Timeline

    # 开启时间轴特性（try/finally 还原；conftest 亦按测试隔离 reload）
    feature_cfg = config_manager._config.setdefault("feature", {})
    prev = feature_cfg.get("timeline_enable")
    feature_cfg["timeline_enable"] = True
    try:
        store = KnowledgeClaimStore(base_dir=tmp_path / "claims")
        bank = SkillBank()

        class _PM:
            def __init__(self):
                self.projects = {}

            async def get_project(self, pid):
                return self.projects.get(pid)

            async def update_project(self, project):
                self.projects[project.project_id] = project

        class _TM:
            async def get_task_detail(self, task_id):
                return {
                    "task_id": task_id, "status": "RUNNING",
                    "progress": {"total": 4, "completed": 2, "percent": 50.0},
                }

        pm = _PM()
        pm.projects["p1"] = AuthorProject(
            project_id="p1", project_name="测试",
            timelines=[Timeline(timeline_id="tl1", name="主线")],
        )
        tm = _TM()

        plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="多域收口工具")
        v1 = plan.draft("多域收口工具", [
            PlanStep(tool="library.quantize_start", args={
                "blocks": ["伏笔设置决定高潮可信度"], "claims": ["叙事高潮前应铺设伏笔"],
                "document_id": "d1",
            }),
            PlanStep(tool="timeline.event_create", args={
                "project_id": "p1", "timeline_id": "tl1", "title": "高光转折",
            }),
            PlanStep(tool="task.quantize_progress", args={"task_id": "q1"}),
        ])
        plan.submit_for_review(v1, "author")
        plan.approve(v1, "author")
        results = await PlanExecutor().execute_plan(
            plan, version=v1, actor="author",
            handlers=build_default_handlers(store, bank, pm, tm),
        )
        by_tool = {r.tool: r for r in results}
        assert by_tool["library.quantize_start"].status == "OK"
        assert by_tool["library.quantize_start"].result["total"] >= 1
        assert by_tool["timeline.event_create"].status == "OK"
        assert by_tool["timeline.event_create"].result["status"] == "OK"
        assert by_tool["task.quantize_progress"].status == "OK"
        assert by_tool["task.quantize_progress"].result["progress"]["percent"] == 50.0
    finally:
        if prev is None:
            feature_cfg.pop("timeline_enable", None)
        else:
            feature_cfg["timeline_enable"] = prev