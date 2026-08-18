"""Ledger-first source registration and outbox regression tests."""
from __future__ import annotations

import pytest

from core.config_manager import config_manager
from services.indexer import CardIndexer
from services.ledger_repository import LedgerRepository


def _legacy_config(monkeypatch) -> None:
    """隔离全局 ledger 灰度：本文件多数用例按 legacy 写链（authoritative=false）验证。"""
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")


@pytest.mark.asyncio
async def test_register_document_writes_passages_and_pending_outbox(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        repository = LedgerRepository(indexer)
        result = await repository.register_document(
            document_id="book-ledger",
            title="Ledger Book",
            source_uri="library/books/book-ledger.txt",
            media_type="text/plain",
            content="甲\n\n乙",
            parser_id="builtin.txt",
            parser_version="1.0",
            passages=[
                {"text": "甲", "char_start": 0, "char_end": 1},
                {"text": "乙", "char_start": 3, "char_end": 4},
            ],
        )
        assert result["passage_count"] == 2
        report = await repository.reconcile_document("book-ledger")
        assert report == {"passages": 2, "claims": 0}
        cursor = await indexer.conn.execute(
            "SELECT status FROM ledger_outbox WHERE artifact_id=?", ("book-ledger",)
        )
        assert (await cursor.fetchone())[0] == "PENDING"
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_card_delete_archives_ledger_artifacts_and_enqueues_outbox(tmp_path, monkeypatch):
    from models.cards import InfoCard, SourceAnchor

    _legacy_config(monkeypatch)
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        card = InfoCard(
            source_book_id="book-delete",
            source_document_id="book-delete",
            source_anchor=SourceAnchor(source_document_id="book-delete", quote="evidence"),
            card_sub_type="note",
            content="claim to delete",
        )
        await indexer.save_card(card)
        assert await indexer.delete_card(card.card_id) is True
        cursor = await indexer.conn.execute(
            "SELECT status FROM ledger_claims WHERE claim_id=?", (card.card_id,)
        )
        assert (await cursor.fetchone())[0] == "archived"
        events = await indexer.list_pending_ledger_outbox()
        assert any(event["operation"] == "CLAIM_TOMBSTONED" for event in events)
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_reconciliation_derived_draft_without_anchor_is_non_blocking(tmp_path, monkeypatch):
    from models.cards import InfoCard
    from services.ledger_reconciliation import reconcile_legacy_cards

    _legacy_config(monkeypatch)
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await indexer.save_card(InfoCard(source_book_id="legacy", card_sub_type="note", content="legacy card"))
        report = await reconcile_legacy_cards(indexer)
        # 默认 status='draft' 的卡是派生草稿：无锚点不阻塞，仅信息性记录
        assert report["derived_draft_count"] == 1
        assert report["missing_anchor"] == []
        assert report["missing_source"] == []
        assert report["ready_for_authoritative"] is True
    finally:
        await indexer.close()
