"""知识域默认 tool handlers 测试（V0.4：一个计划跑多个真实受控工具）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_metrics import CandidateKnowledge, EvidenceRef
from services.knowledge_tools import make_default_knowledge_handlers, make_quantize_start_handler, search_claims
from services.plan_executor import PlanExecutor
from services.skill_versioning import SkillVersionedSkill


@pytest.mark.asyncio
async def test_search_cards_returns_ordered_matches(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    await store.add_candidate(CandidateKnowledge(
        claim="写作要把握读者情绪节奏",
        evidence=[EvidenceRef(document_id="d1", chapter="c1", quote="写作要把握读者情绪节奏")],
    ))
    hits = await search_claims("情绪节奏", limit=5, store=store)
    assert hits and hits[0]["claim"] == "写作要把握读者情绪节奏"
    # 无关查询 → 空
    assert await search_claims("量子物理", limit=5, store=store) == []


@pytest.mark.asyncio
async def test_approved_plan_runs_two_real_tools(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    await store.add_candidate(CandidateKnowledge(
        claim="因果推理需可证伪证据",
        evidence=[EvidenceRef(document_id="d1", chapter="c1", quote="因果推理需可证伪证据")],
    ))
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="检索与一致性")
    v1 = plan.draft(
        "检索与一致性",
        [
            PlanStep(tool="library.search_cards", args={"query": "因果", "limit": 5}),
            PlanStep(tool="knowledge.consistency_check", args={}),
        ],
    )
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    executor = PlanExecutor()
    results = await executor.execute_plan(
        plan, version=v1, actor="author",
        handlers=make_default_knowledge_handlers(store),
    )
    by_tool = {r.tool: r for r in results}
    assert by_tool["library.search_cards"].status == "OK"
    assert by_tool["library.search_cards"].result["count"] >= 1
    assert by_tool["knowledge.consistency_check"].status == "OK"
    assert by_tool["knowledge.consistency_check"].result["total_claims"] == 1


@pytest.mark.asyncio
async def test_quantize_start_runs_refine_in_approved_plan(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path / "claims")
    skill = SkillVersionedSkill("skill.extracted", "1.0.0")
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="发起量化")
    v1 = plan.draft(
        "发起量化",
        [PlanStep(tool="library.quantize_start", args={
            "document_id": "d1",
            "blocks": ["科学方法强调因果推理需要可证伪的证据。"],
            "claims": ["因果推理需可证伪证据"],
        })],
    )
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    results = await PlanExecutor().execute_plan(
        plan, version=v1, actor="author",
        handlers={"library.quantize_start": make_quantize_start_handler(store, {"skill.extracted": skill})},
    )
    assert results[0].status == "OK"
    out = results[0].result
    assert out["total"] == 1 and out["stored"] == 1
    assert out["proposed"] == ["skill.extracted@1.1.0-candidate"]
    assert out["details"][0]["evidence_count"] >= 1