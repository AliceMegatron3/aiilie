"""证据绑定测试（V0.2 主链路：段落→观点→证据 → 证据门）。"""
from __future__ import annotations

import pytest

from services.evidence_binder import bind_evidence
from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_metrics import CandidateKnowledge


def test_binds_top_matching_passages():
    claim = "写作要把握读者情绪节奏"
    text = [
        ("第一章", "作者认为把握读者情绪节奏是核心。"),
        ("第二章", "量子物理与小说完全无关。"),
        ("第二章", "情绪节奏要张弛有度。"),
    ]
    refs = bind_evidence(claim, text, document_id="d1")
    assert refs
    assert refs[0].document_id == "d1"
    # 相关段落排在前面
    assert "情绪节奏" in refs[0].quote
    assert len(refs) <= 3
    assert refs[0].location.startswith("para#")


def test_no_match_returns_empty():
    refs = bind_evidence("关于税收制度", ["雨水丰沛，作物生长。", "远处的山峦连绵。"], document_id="d1")
    assert refs == []


@pytest.mark.asyncio
async def test_bind_then_evidence_gate_chain(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    claim = "因果推理需可证伪证据"
    refs = bind_evidence(claim, ["科学方法强调因果推理需要可证伪的证据。"], document_id="d1", chapter="第一章")
    assert refs
    candidate = CandidateKnowledge(claim=claim, evidence=refs)
    out = await store.add_candidate(candidate)
    assert out.action in ("stored", "conflict", "duplicate")
    # 无匹配 → 无证据 → 证据门拒绝（不落库）
    no_refs = bind_evidence("无关话题XYZ", ["雨水丰沛，作物生长。"], document_id="d1")
    assert no_refs == []
    out2 = await store.add_candidate(CandidateKnowledge(claim="无关话题XYZ", evidence=no_refs))
    assert out2.action == "no_evidence"