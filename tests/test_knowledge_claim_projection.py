"""量化主张 → 权威 Ledger 落库与技能语料打通测试（V0.2 收口）。

覆盖：
- 投影后落库进权威 Ledger（get_ledger_document 返回主张/证据，幂等无重复）；
- 量化指标落库 ledger_metrics（evidence_count/rule_confidence 等 + source_claim_ids）；
- 幂等（重复同步不产生重复主张/指标/outbox）；
- 证据门 fail-closed（无证据主张拒绝投影）；
- 技能语料摘要（claim/证据原文/条件/步骤/rule_confidence）。
"""
from __future__ import annotations

import pytest

from services.indexer import CardIndexer
from services.knowledge_claim_projection import (
    claims_to_skill_corpus,
    project_claim_to_ledger,
    sync_claims_to_indexer,
)
from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_metrics import CandidateKnowledge, EvidenceRef

CLAIM_A = "叙事高潮前应当铺设伏笔，以保证转折具备逻辑铺垫与情感共鸣。"
CLAIM_B = "人物成长需要经历挫折与反思，才能实现真正的性格蜕变。"
EVIDENCE_A = [
    EvidenceRef(document_id="d1", chapter="第一章", quote="伏笔的设置决定了高潮的可信度。"),
    EvidenceRef(document_id="d1", chapter="第一章", quote="缺乏铺垫的转折显得突兀。"),
    EvidenceRef(document_id="d2", chapter="第二章", quote="情感共鸣依赖前文的积累。"),
]
EVIDENCE_B = [
    EvidenceRef(document_id="d3", chapter="第三章", quote="经历磨难后角色才开始转变。"),
]


@pytest.fixture
async def claim_store(tmp_path) -> KnowledgeClaimStore:
    return KnowledgeClaimStore(base_dir=tmp_path / "claims")


@pytest.fixture
async def indexer(tmp_path) -> CardIndexer:
    idx = CardIndexer(index_dir=tmp_path / "idx")
    await idx.initialize()
    yield idx
    await idx.close()


async def _seed_claim(store: KnowledgeClaimStore, claim: str, evidence) -> dict:
    outcome = await store.add_candidate(
        CandidateKnowledge(
            claim=claim, evidence=evidence,
            conditions=["长篇小说"], steps=["埋设伏笔", "验证转折"],
        )
    )
    assert outcome.action in ("stored", "conflict")
    return (await store.list_claims())[-1]


@pytest.mark.asyncio
async def test_project_claim_to_ledger_persisted(claim_store, indexer):
    record = await _seed_claim(claim_store, CLAIM_A, EVIDENCE_A)
    claim_id = await project_claim_to_ledger(indexer, record)
    assert claim_id == record["candidate_id"]
    # 主张落库进权威 Ledger（可经既有 get_ledger_document 检索）
    doc = await indexer.get_ledger_document("d1")
    assert doc is not None
    claim_rows = [c for c in doc["claims"] if c["claim_id"] == claim_id]
    assert len(claim_rows) == 1
    assert claim_rows[0]["content"] == CLAIM_A
    assert len(doc["evidence"]) == 2  # d1 下的两条证据；第三条属于 d2
    doc2 = await indexer.get_ledger_document("d2")
    assert len(doc2["evidence"]) == 1
    assert float(claim_rows[0]["confidence"]) > 0  # rule_confidence 落库


@pytest.mark.asyncio
async def test_project_claim_ledger_metrics(claim_store, indexer):
    record = await _seed_claim(claim_store, CLAIM_A, EVIDENCE_A)
    claim_id = await project_claim_to_ledger(indexer, record)
    metrics = await indexer.list_ledger_metrics(document_id="d1")
    names = {m["name"] for m in metrics}
    assert {"evidence_count", "rule_confidence", "independent_chapter_count"} <= names
    assert any(m["source_claim_ids"] == [claim_id] for m in metrics)


@pytest.mark.asyncio
async def test_sync_claims_to_indexer_idempotent(claim_store, indexer):
    await _seed_claim(claim_store, CLAIM_A, EVIDENCE_A)
    await _seed_claim(claim_store, CLAIM_B, EVIDENCE_B)
    first = await sync_claims_to_indexer(claim_store, indexer)
    assert first["total"] == 2 and first["projected"] == 2 and first["skipped"] == 0
    # 再次同步：幂等，不产生重复主张
    second = await sync_claims_to_indexer(claim_store, indexer)
    assert second["projected"] == 2
    doc_a = await indexer.get_ledger_document("d1")
    doc_b = await indexer.get_ledger_document("d3")
    assert len(doc_a["claims"]) == 1 and len(doc_b["claims"]) == 1
    assert len(doc_a["metrics"]) >= 9 and len(doc_b["metrics"]) >= 9  # 指标不翻倍


@pytest.mark.asyncio
async def test_evidence_gate_fail_closed(indexer):
    record = {"candidate_id": "claim_no_ev", "claim": "无证据主张", "evidence": []}
    assert await project_claim_to_ledger(indexer, record) is None


@pytest.mark.asyncio
async def test_claims_to_skill_corpus(claim_store):
    await _seed_claim(claim_store, CLAIM_A, EVIDENCE_A)
    entries = await claims_to_skill_corpus(claim_store)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["claim"] == CLAIM_A
    assert len(entry["evidence_quotes"]) == 3
    assert entry["conditions"] == ["长篇小说"]
    assert entry["steps"] == ["埋设伏笔", "验证转折"]
    assert entry["rule_confidence"] > 0
    assert set(entry) >= {
        "candidate_id", "claim", "evidence_quotes", "conditions",
        "counterexamples", "steps", "rule_confidence", "evidence_count",
        "flagged_conflict",
    }
