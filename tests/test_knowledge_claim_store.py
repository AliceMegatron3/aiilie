"""知识主张存储去重/冲突门测试（V0.2 量化拆书）。"""
from __future__ import annotations

import pytest

from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_metrics import CandidateKnowledge, EvidenceRef


async def _add(store, claim, **kw):
    cand = CandidateKnowledge(claim=claim, evidence=[EvidenceRef(document_id="d1", chapter="c1", quote=claim)])
    return await store.add_candidate(cand, **kw)


@pytest.mark.asyncio
async def test_no_evidence_rejected(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    out = await store.add_candidate(CandidateKnowledge(claim="无来源观点"))
    assert out.action == "no_evidence"
    assert len(await store.list_claims()) == 0  # 不落库


@pytest.mark.asyncio
async def test_store_first_claim_and_replay(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    out = await _add(store, "因果推理需可证伪证据")
    assert out.action in ("stored", "conflict")
    stored = await store.list_claims()
    assert len(stored) == 1
    assert stored[0]["metrics"]["evidence_count"] == 1


@pytest.mark.asyncio
async def test_duplicate_detected_and_not_duplicated(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    await _add(store, "写作需要把握情感节奏与张力")
    # 相同主张（内容哈希一致）：判定重复，不重复入库
    out = await _add(store, "写作需要把握情感节奏与张力")
    assert out.action == "duplicate"
    assert out.existing_id
    assert len(await store.list_claims()) == 1


@pytest.mark.asyncio
async def test_distinct_claim_stored(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    await _add(store, "写作需要把握情感节奏")
    out = await _add(store, "量子物理与小说技巧完全无关")
    assert out.action in ("stored", "conflict")
    assert len(await store.list_claims()) == 2


@pytest.mark.asyncio
async def test_conflict_flagged(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    # 高度近义但内容不同、且共享反例 → 计入冲突并标记
    first = CandidateKnowledge(
        claim="断更一定会损失读者黏性",
        counterexamples=["编辑实测案例X"],
        evidence=[EvidenceRef(document_id="d1", chapter="c", quote="断更一定会损失读者黏性")],
    )
    await store.add_candidate(first)
    second = CandidateKnowledge(
        claim="断更多少会损失读者黏性",
        counterexamples=["编辑实测案例X"],
        evidence=[EvidenceRef(document_id="d2", chapter="c", quote="断更多少会损失读者黏性")],
    )
    out = await store.add_candidate(second)
    assert out.action == "conflict"
    stored = [s for s in await store.list_claims() if s["flagged_conflict"]]
    assert any(s["claim"] == "断更多少会损失读者黏性" for s in stored)