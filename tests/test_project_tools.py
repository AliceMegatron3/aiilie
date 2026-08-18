"""项目查询 tool 测试（V0.4：project.query 跨 store 运行态）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.document_draft import DraftStore
from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_metrics import CandidateKnowledge, EvidenceRef
from services.plan_executor import PlanExecutor
from services.project_tools import make_project_query_handler, project_status
from services.skill_bank import SkillBank


@pytest.mark.asyncio
async def test_project_status_aggregates(tmp_path):
    claims = KnowledgeClaimStore(base_dir=tmp_path / "claims")
    await claims.add_candidate(CandidateKnowledge(
        claim="因果推理需可证伪证据",
        evidence=[EvidenceRef(document_id="d1", chapter="c1", quote="因果推理需可证伪证据")],
    ))
    drafts = DraftStore(base_dir=tmp_path / "drafts")
    await drafts.create("proj_a", "草稿A")
    bank = SkillBank()
    bank.get("skill.extracted", "1.0.0").propose_candidate("minor")

    st = await project_status(claims, drafts, bank)
    assert st["claim_count"] == 1
    assert st["draft_count"] == 1
    assert st["active_skills"] == {"skill.extracted": "skill.extracted@1.0.0"}
    assert st["skill_candidate_count"] == 1


@pytest.mark.asyncio
async def test_project_query_runs_in_approved_plan(tmp_path):
    claims = KnowledgeClaimStore(base_dir=tmp_path / "claims")
    drafts = DraftStore(base_dir=tmp_path / "drafts")
    bank = SkillBank()
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="查项目")
    v1 = plan.draft("查项目", [PlanStep(tool="project.query", args={})])
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    results = await PlanExecutor().execute_plan(
        plan, version=v1, actor="author",
        handlers={"project.query": make_project_query_handler(claims, drafts, bank)},
    )
    assert results[0].status == "OK"
    assert results[0].result["claim_count"] == 0
    assert results[0].result["draft_count"] == 0