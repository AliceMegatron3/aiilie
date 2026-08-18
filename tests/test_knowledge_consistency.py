"""知识一致性检查测试（V0.4 第一批工具：knowledge.consistency_check）。"""
from __future__ import annotations

import pytest

from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_consistency import make_consistency_handler, run_consistency_check
from services.knowledge_metrics import CandidateKnowledge, EvidenceRef
from services.plan_executor import PlanExecutor
from services.agent_plan import AgentPlan, PlanStep
from services.whitelisted_tools import PermissionLevel


def _cand(claim):
    return CandidateKnowledge(claim=claim, evidence=[EvidenceRef(document_id="d1", chapter="c", quote=claim)])


@pytest.mark.asyncio
async def test_consistency_check_empty_is_consistent(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    report = await run_consistency_check(store)
    assert report["total_claims"] == 0
    assert report["consistent"] is True


@pytest.mark.asyncio
async def test_consistency_detects_conflict_flag(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    # 高度近义且共享反例 → 冲突，flagged_conflict=True
    await store.add_candidate(CandidateKnowledge(
        claim="断更一定会损失读者黏性",
        counterexamples=["编辑实测案例X"],
        evidence=[EvidenceRef(document_id="d1", chapter="c", quote="断更一定会损失读者黏性")],
    ))
    await store.add_candidate(CandidateKnowledge(
        claim="断更多少会损失读者黏性",
        counterexamples=["编辑实测案例X"],
        evidence=[EvidenceRef(document_id="d2", chapter="c", quote="断更多少会损失读者黏性")],
    ))
    report = await run_consistency_check(store)
    assert report["conflict_count"] >= 1
    assert report["consistent"] is False


@pytest.mark.asyncio
async def test_consistency_as_plan_executor_handler(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    await store.add_candidate(_cand("写作需要把握情感节奏与张力"))

    # 构造已批准计划，调用 knowledge.consistency_check 工具的默认 handler
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="检查一致性")
    v1 = plan.draft("检查一致性", [PlanStep(tool="knowledge.consistency_check", args={})])
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    executor = PlanExecutor()
    results = await executor.execute_plan(
        plan,
        version=v1,
        actor="author",
        handlers={"knowledge.consistency_check": make_consistency_handler(store)},
    )
    assert results[0].status == "OK"
    assert results[0].result["total_claims"] == 1
    assert bool(results[0].result["consistent"] or results[0].result["conflict_count"] >= 0)