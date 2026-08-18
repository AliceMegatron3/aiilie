"""Canonical Ledger read facade with card-shaped compatibility results."""
from __future__ import annotations

import json
import logging
from typing import Any

from core.config_manager import config_manager

logger = logging.getLogger(__name__)


class LedgerReadFacade:
    def __init__(self, indexer: Any) -> None:
        self.indexer = indexer

    @property
    def enabled(self) -> bool:
        return config_manager.get_bool("ledger.authoritative", False) or config_manager.get("ledger.read_mode", "legacy") == "ledger"

    async def is_readable(self) -> bool:
        """Batch 3：readability = 配置上走 Ledger 读 + **readiness 通过**。

        未通过 readiness（迁移未完成/对账未闭合等）时返回 False，由调用方
        **回退 legacy 读或拒绝 read**，绝不静默从不可用/未就绪的 Ledger 表读取。
        """
        if not self.enabled:
            return False
        try:
            from services.ledger_readiness import LedgerReadiness

            report = await LedgerReadiness(self.indexer).report()
            return bool(report.get("ready_for_authoritative", False))
        except Exception as exc:  # noqa: BLE001
            # readiness 判定异常 → fail-closed：不可读，宁可回退 legacy/拒绝
            logger.warning("[LedgerReadFacade] readiness 判定失败，回退 legacy: %s", exc)
            return False

    async def search_cards(self, *, keyword: str | None = None, source_book: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        conditions = ["c.status != 'archived'", "d.status != 'archived'"]
        args: list[Any] = []
        if source_book:
            conditions.append("(c.document_id = ? OR c.document_id IN (SELECT document_id FROM ledger_documents WHERE document_id = ?))")
            args.extend((source_book, source_book))
        if status:
            conditions.append("c.status = ?")
            args.append(status)
        if keyword:
            conditions.append("c.content LIKE ?")
            args.append(f"%{keyword}%")
        args.extend((max(1, min(int(limit), 200)), max(0, int(offset))))
        cursor = await self.indexer.conn.execute(
            f"""SELECT c.claim_id AS card_id, c.document_id AS source_book, '' AS source_chapter,
                       'info' AS card_type, c.claim_type AS knowledge_type, c.content,
                       c.confidence AS utility_score, c.status, c.fingerprint,
                       c.evidence_ids, d.title AS document_title
                FROM ledger_claims c JOIN ledger_documents d ON d.document_id=c.document_id
                WHERE {' AND '.join(conditions)} ORDER BY c.created_at DESC, c.claim_id LIMIT ? OFFSET ?""",
            args,
        )
        rows = []
        for row in await cursor.fetchall():
            item = dict(row)
            try:
                item["evidence_ids"] = json.loads(item.get("evidence_ids") or "[]")
            except (TypeError, ValueError):
                item["evidence_ids"] = []
            item["source_book_id"] = item.get("source_book", "")
            item["source_document_id"] = item.get("source_book", "")
            item["source_anchor"] = {"source_document_id": item.get("source_book", "")}
            item["tags"] = ["ledger"]
            rows.append(item)
        return rows

    async def count_cards(self, *, keyword: str | None = None, source_book: str | None = None, status: str | None = None) -> int:
        conditions = ["c.status != 'archived'", "d.status != 'archived'"]
        args: list[Any] = []
        if source_book:
            conditions.append("c.document_id = ?")
            args.append(source_book)
        if status:
            conditions.append("c.status = ?")
            args.append(status)
        if keyword:
            conditions.append("c.content LIKE ?")
            args.append(f"%{keyword}%")
        cursor = await self.indexer.conn.execute(
            f"SELECT COUNT(*) FROM ledger_claims c JOIN ledger_documents d ON d.document_id=c.document_id WHERE {' AND '.join(conditions)}",
            args,
        )
        return int((await cursor.fetchone())[0])

    async def get_card_detail(self, card_id: str) -> dict[str, Any] | None:
        cursor = await self.indexer.conn.execute(
            """SELECT c.*, d.title AS document_title, d.source_uri, d.content_hash
               FROM ledger_claims c JOIN ledger_documents d ON d.document_id=c.document_id
               WHERE c.claim_id=? AND c.status != 'archived' AND d.status != 'archived'""",
            (card_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        item = dict(row)
        item["card_id"] = item["claim_id"]
        item["source_book_id"] = item["document_id"]
        item["source_document_id"] = item["document_id"]
        item["content"] = item.get("content", "")
        item["tags"] = ["ledger"]
        item["source_anchor"] = {"source_document_id": item["document_id"], "quote": item["content"][:200]}
        return item

    async def delete_card(self, card_id: str) -> bool:
        """Authoritative 删除 = 归档 ledger claim（及关联），返回是否真的删到 claim。

        权威模式下 cards 表为只读/空投影，不能依赖它判删除；必须直接归档 ledger_claims，
        且未命中时应返回 False 而非误报成功（与旧 cards 删除语义一致）。
        """
        cursor = await self.indexer.conn.execute(
            "UPDATE ledger_claims SET status='archived', updated_at=datetime('now') WHERE claim_id=? AND status != 'archived'",
            (card_id,),
        )
        await self.indexer.conn.commit()
        archived = int(cursor.rowcount) > 0
        # 关联行冻结（尽力而为）
        await self.indexer.conn.execute(
            "UPDATE ledger_metrics SET status='archived' WHERE metric_id=?", (f"metric_{card_id}",)
        )
        await self.indexer.conn.execute(
            "UPDATE ledger_evidence SET status='archived' WHERE evidence_id=?", (f"evidence_{card_id}",)
        )
        await self.indexer.conn.execute(
            "UPDATE ledger_relations SET status='archived' WHERE source_id=? OR target_id=?",
            (card_id, card_id),
        )
        await self.indexer.conn.commit()
        return archived
