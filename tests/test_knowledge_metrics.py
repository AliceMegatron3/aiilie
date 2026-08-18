"""确定性量化指标测试（V0.2 量化拆书，不依赖 LLM）。"""
from __future__ import annotations

from services.knowledge_metrics import (
    CandidateKnowledge,
    EvidenceRef,
    ExistingClaim,
    compute_knowledge_metrics,
)


def test_empty_candidate_yields_zero_struct_metrics():
    m = compute_knowledge_metrics(CandidateKnowledge(claim="观点"))
    assert m["evidence_count"] == 0
    assert m["independent_chapter_count"] == 0
    assert m["source_coverage"] == 0.0
    assert m["counterexample_count"] == 0
    assert m["executable_step_count"] == 0
    assert m["conflict_count"] == 0
    assert m["max_similarity_to_existing"] == 0.0


def test_evidence_and_source_coverage_counts():
    cand = CandidateKnowledge(
        claim="因果推理需可证伪证据",
        evidence=[
            EvidenceRef(document_id="d1", chapter="c1", quote="a"),
            EvidenceRef(document_id="d1", chapter="c1", quote="b"),
            EvidenceRef(document_id="d2", chapter="c2", quote="c"),
        ],
    )
    m = compute_knowledge_metrics(cand)
    assert m["evidence_count"] == 3
    # 独立(文档,章节)对 = 2（d1.c1、d2.c2）；独立文档 = 2
    assert m["independent_chapter_count"] == 2
    assert abs(m["source_coverage"] - 2 / 3) < 1e-3


def test_repetition_and_similarity_detect_duplicate():
    cand = CandidateKnowledge(claim="写作需要把握情感节奏")
    existing = [
        ExistingClaim(claim="创作要把握读者情绪节奏", rejected=False),
        ExistingClaim(claim="量子物理完全无关", rejected=False),
    ]
    m = compute_knowledge_metrics(cand, existing=existing)
    # 应与近义主张高相似
    assert m["max_similarity_to_existing"] > 0.3
    assert m["claim_repetition_ratio"] == m["max_similarity_to_existing"]


def test_conflict_counts_rejected_or_shared_counterexample():
    cand = CandidateKnowledge(
        claim="断更无损读者黏性",
        counterexamples=["编辑实测案例X"],
    )
    existing = [
        ExistingClaim(claim="断更无损读者黏性", rejected=True),          # 相似且被驳回 => 冲突
        ExistingClaim(claim="断更无损读者黏性", counterexamples=["编辑实测案例X"]),  # 相似且共享反例 => 冲突
        ExistingClaim(claim="断更无损读者黏性", rejected=False),          # 相似但非冲突
        ExistingClaim(claim="完全无关主题", rejected=True),              # rejected 但不相识 => 不计
    ]
    m = compute_knowledge_metrics(cand, existing=existing)
    assert m["conflict_count"] == 2
    assert m["max_similarity_to_existing"] > 0.5


def test_condition_completeness_heuristic():
    bare = CandidateKnowledge(claim="需说明适用边界")
    wide = CandidateKnowledge(claim="需说明适用边界", conditions=["当对象是作者", "如果篇幅长", "边界=学术文"])
    m_bare = compute_knowledge_metrics(bare)
    m_wide = compute_knowledge_metrics(wide)
    assert m_wide["condition_completeness"] > m_bare["condition_completeness"]


def test_steps_and_confidence_bounded():
    cand = CandidateKnowledge(
        claim="发布前做一致性检查",
        steps=["步骤1", "步骤2", "步骤3", "步骤4", "步骤5", "步骤6"],
        conditions=["对象=正式技能"],
    )
    m = compute_knowledge_metrics(cand)
    assert m["executable_step_count"] == 6
    # rule_confidence 应在 [0,1]
    assert 0.0 <= m["rule_confidence"] <= 1.0