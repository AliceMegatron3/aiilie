"""量化拆书 — 量化流水线落库与 indexer/技能语料打通（V0.2 收口）。

方向报告「三.2」：量化主张保留证据与规则结果，作为辅助判断持久化；
「三.1」：没有证据来源的内容不能直接进入正式技能库。既有构件：

- `KnowledgeClaimStore`：文件态主张存储（证据门+去重/冲突门，可回放）；
- `CardIndexer`：SQLite 热索引 + Ledger 权威库（ledger_documents/passages/
  evidence/claims/metrics/outbox），是既有知识检索基础设施（7.7 权威方向）。

本模块把两者打通——已入库（带证据）的量化主张**投影进权威 Ledger**：
主张 → `ledger_claims`（含 rule_confidence、指纹去重）、每条证据 →
`ledger_evidence`/`ledger_passages`/`ledger_documents`、量化指标 → `ledger_metrics`、
`CLAIM_QUANTIFIED` 审计事件 → outbox。全部幂等（稳定 ID + ON CONFLICT upsert），
直接写 Ledger 表，不依赖旧的 `cards` 投影表（兼容旧库 schema 差异）。

同时产出**技能语料摘要**（claim/证据原文/适用条件/步骤/rule_confidence），
供反思与技能炼制作为语料来源。

- `project_claim_to_ledger`：单条主张投影进 Ledger（幂等）；
- `sync_claims_to_indexer`：把全部已入库主张同步进权威 Ledger（幂等）；
- `claim_to_corpus_entry` / `claims_to_skill_corpus`：技能语料摘要。

验证：`tests/test_knowledge_claim_projection.py`（Ledger 落库 / metrics 落库 /
幂等 / 证据门 fail-closed / 语料摘要）＋ `test_integration.py::test_knowledge_claim_sync_endpoint`
＋ `test_knowledge_corpus_endpoint`。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from services.indexer import CardIndexer
from services.knowledge_claim_store import KnowledgeClaimStore

_METHOD = "knowledge_claim_projection"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _claim_fingerprint(claim_id: str, claim: str) -> str:
    return hashlib.sha256("|".join((claim_id, claim)).encode("utf-8")).hexdigest()


async def _project_to_ledger(indexer: CardIndexer, record: dict[str, Any], claim_id: str) -> None:
    """把一条主张投影进权威 Ledger 表（幂等：稳定 ID + ON CONFLICT upsert）。"""
    now = _now_iso()
    claim = str(record.get("claim") or "").strip()
    metrics = record.get("metrics") or {}
    evidence = record.get("evidence") or []
    rule_confidence = float(metrics.get("rule_confidence", 0.0) or 0.0)
    evidence_ids: list[str] = []
    document_ids: set[str] = set()

    for i, ev in enumerate(evidence):
        document_id = str(ev.get("document_id") or "")
        if not document_id:
            continue
        chapter = str(ev.get("chapter") or "")
        location = str(ev.get("location") or "")
        quote = str(ev.get("quote") or "")
        document_ids.add(document_id)
        passage_id = f"psg_{claim_id}_{i}"
        evidence_id = f"ev_{claim_id}_{i}"
        evidence_ids.append(evidence_id)
        await indexer.conn.execute(
            """INSERT INTO ledger_documents(document_id, title, source_uri, content_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id) DO UPDATE SET updated_at=excluded.updated_at""",
            (document_id, document_id, location, _claim_fingerprint(claim_id, quote), now, now),
        )
        await indexer.conn.execute(
            """INSERT INTO ledger_passages
               (passage_id, document_id, sequence, heading, quote, text_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
               ON CONFLICT(passage_id) DO UPDATE SET
                 heading=excluded.heading, quote=excluded.quote, text_hash=excluded.text_hash""",
            (passage_id, document_id, i, chapter, quote, _claim_fingerprint(claim_id, quote), now),
        )
        await indexer.conn.execute(
            """INSERT INTO ledger_evidence
               (evidence_id, document_id, passage_id, quote, anchor, evidence_level, confidence, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'C', ?, 'verified', ?)
               ON CONFLICT(evidence_id) DO UPDATE SET
                 quote=excluded.quote, anchor=excluded.anchor, confidence=excluded.confidence""",
            (evidence_id, document_id, passage_id, quote,
             json.dumps({"location": location, "chapter": chapter}, ensure_ascii=False, default=str),
             rule_confidence, now),
        )

    if not document_ids:
        return
    await indexer.conn.execute(
        """INSERT INTO ledger_claims
           (claim_id, document_id, evidence_ids, claim_type, content, fingerprint,
            scope_level, confidence, status, created_at, updated_at)
           VALUES (?, ?, ?, 'FACT', ?, ?, 'book', ?, 'draft', ?, ?)
           ON CONFLICT(claim_id) DO UPDATE SET
             evidence_ids=excluded.evidence_ids, content=excluded.content,
             fingerprint=excluded.fingerprint, confidence=excluded.confidence,
             status=excluded.status, updated_at=excluded.updated_at""",
        (claim_id, sorted(document_ids)[0], json.dumps(evidence_ids, ensure_ascii=False),
         claim, _claim_fingerprint(claim_id, claim), rule_confidence, now, now),
    )

    for name, value in metrics.items():
        await indexer.conn.execute(
            """INSERT INTO ledger_metrics
               (metric_id, document_id, name, value, method, sample_size, semantic_level,
                source_claim_ids, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(metric_id) DO UPDATE SET
                 value=excluded.value, sample_size=excluded.sample_size, status=excluded.status""",
            (
                f"metric_{claim_id}_{name}", sorted(document_ids)[0], name,
                json.dumps(value, ensure_ascii=False, default=str),
                _METHOD, 1, "descriptive",
                json.dumps([claim_id], ensure_ascii=False), "draft", now,
            ),
        )

    await indexer.conn.execute(
        """INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at)
           VALUES (?, ?, 'CLAIM_QUANTIFIED', ?, 'PENDING', ?)
           ON CONFLICT(event_id) DO UPDATE SET payload=excluded.payload, status='PENDING', applied_at=NULL""",
        (
            f"claim_quantified:{claim_id}", claim_id,
            json.dumps({"claim_id": claim_id, "document_id": sorted(document_ids)[0]}, ensure_ascii=False),
            now,
        ),
    )
    await indexer.conn.commit()


async def project_claim_to_ledger(indexer: CardIndexer, record: dict[str, Any]) -> str | None:
    """把一条已入库量化主张投影进权威 Ledger（幂等）。

    无证据 → 返回 None 且不写库（fail-closed，与证据门一致）。
    """
    if not (record.get("evidence") or []):
        return None
    claim_id = str(record.get("candidate_id") or "").strip()
    if not claim_id:
        claim = str(record.get("claim") or "").strip()
        if not claim:
            return None
        claim_id = "claim_" + hashlib.sha256(claim.encode("utf-8")).hexdigest()[:16]
    await _project_to_ledger(indexer, record, claim_id)
    return claim_id


async def sync_claims_to_indexer(
    store: KnowledgeClaimStore, indexer: CardIndexer
) -> dict[str, Any]:
    """把全部已入库主张同步进权威 Ledger（幂等，可重复调用）。"""
    claims = await store.list_claims()
    projected: list[str] = []
    skipped = 0
    for record in claims:
        if not (record.get("evidence") or []):
            skipped += 1
            continue
        claim_id = await project_claim_to_ledger(indexer, record)
        if claim_id:
            projected.append(claim_id)
    return {
        "total": len(claims),
        "projected": len(projected),
        "skipped": skipped,
        "claim_ids": projected,
    }


def claim_to_corpus_entry(record: dict[str, Any]) -> dict[str, Any]:
    """单条主张 → 技能语料摘要（供反思/技能炼制作为语料来源）。"""
    metrics = record.get("metrics") or {}
    return {
        "candidate_id": record.get("candidate_id", ""),
        "claim": record.get("claim", ""),
        "evidence_quotes": [str(e.get("quote") or "") for e in (record.get("evidence") or [])],
        "conditions": record.get("conditions", []),
        "counterexamples": record.get("counterexamples", []),
        "steps": record.get("steps", []),
        "rule_confidence": metrics.get("rule_confidence", 0.0),
        "evidence_count": metrics.get("evidence_count", 0),
        "flagged_conflict": record.get("flagged_conflict", False),
    }


async def claims_to_skill_corpus(store: KnowledgeClaimStore) -> list[dict[str, Any]]:
    """已入库主张 → 技能语料清单（含证据原文/条件/步骤/rule_confidence）。"""
    return [claim_to_corpus_entry(r) for r in await store.list_claims()]


__all__ = [
    "claim_to_corpus_entry",
    "claims_to_skill_corpus",
    "project_claim_to_ledger",
    "sync_claims_to_indexer",
]
