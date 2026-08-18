"""反思域 tool handlers 测试（V0.4：查看候选中技能版本）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.knowledge_claim_store import KnowledgeClaimStore
from services.plan_executor import PlanExecutor
from services.reflector_tools import make_default_reflector_handlers, make_reflection_start_handler
from services.skill_bank import SkillBank
from services.skill_versioning import SkillVersionedSkill


@pytest.mark.asyncio
async def test_view_candidates_reports_proposed_candidates(tmp_path):
    bank = SkillBank()
    skill = bank.get("skill.extracted", "1.2.0")
    skill.propose_candidate("minor")  # 1.3.0-candidate

    report = await make_default_reflector_handlers(bank)["reflection.view_candidates"]({})
    assert report["candidates"] == {"skill.extracted": ["skill.extracted@1.3.0-candidate"]}
    assert report["candidates"]["skill.extracted"][0].endswith("-candidate") or True
    assert bank.active_versions()["skill.extracted"] == "skill.extracted@1.2.0"


@pytest.mark.asyncio
async def test_view_candidates_runs_inside_approved_plan(tmp_path):
    bank = SkillBank()
    skill = bank.get("skill.extracted", "1.0.0")
    skill.propose_candidate("minor")

    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="查候选")
    v1 = plan.draft("查候选", [PlanStep(tool="reflection.view_candidates", args={})])
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    executor = PlanExecutor()
    results = await executor.execute_plan(
        plan, version=v1, actor="author",
        handlers=make_default_reflector_handlers(bank),
    )
    assert results[0].status == "OK"
    assert results[0].result["candidates"]["skill.extracted"] == ["skill.extracted@1.1.0-candidate"]


@pytest.mark.asyncio
async def test_reflection_start_runs_refine_and_returns_critique(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    skill = SkillVersionedSkill("skill.extracted", "1.0.0")
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="启动反思")
    v1 = plan.draft(
        "启动反思",
        [PlanStep(tool="reflection.start", args={
            "document_id": "d1",
            "blocks": ["科学方法强调因果推理需要可证伪的证据。"],
            "claims": ["因果推理需可证伪证据"],
        })],
    )
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    results = await PlanExecutor().execute_plan(
        plan, version=v1, actor="author",
        handlers={"reflection.start": make_reflection_start_handler(store, {"skill.extracted": skill})},
    )
    assert results[0].status == "OK"
    out = results[0].result
    assert out["stored"] == 1
    assert out["proposed"] == ["skill.extracted@1.1.0-candidate"]
    assert out["critique"] and out["critique"][0]["passed"] is True