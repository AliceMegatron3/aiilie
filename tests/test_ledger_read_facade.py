"""Canonical Ledger read facade tests."""
from __future__ import annotations

import pytest

from services.indexer import CardIndexer
from services.ledger_read_facade import LedgerReadFacade
from services.ledger_repository import LedgerRepository


@pytest.mark.asyncio
async def test_facade_search_reads_claims_and_hides_archived(tmp_path, monkeypatch):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        repository = LedgerRepository(indexer)
        await repository.register_document(
            document_id="doc-1", title="Doc", source_uri="doc.txt", media_type="text/plain",
            content="claim text", parser_id="builtin.txt", parser_version="1",
            passages=[{"text": "claim text", "char_start": 0, "char_end": 10}],
        )
        await indexer.conn.execute(
            "INSERT INTO ledger_evidence(evidence_id, document_id, passage_id, quote, anchor, created_at) VALUES ('e-1','doc-1','passage_doc-1_000000','claim text','{}',datetime('now'))"
        )
        await indexer.conn.execute(
            "INSERT INTO ledger_claims(claim_id, document_id, evidence_ids, content, fingerprint, created_at, updated_at) VALUES ('claim-1','doc-1','[\"e-1\"]','claim text','fingerprint-1',datetime('now'),datetime('now'))"
        )
        await indexer.conn.commit()
        facade = LedgerReadFacade(indexer)
        monkeypatch.setattr("services.ledger_read_facade.config_manager.get", lambda key, default=None: "ledger" if key == "ledger.read_mode" else default)
        rows = await facade.search_cards(keyword="claim")
        assert rows[0]["card_id"] == "claim-1"
        assert await facade.count_cards(keyword="claim") == 1
        await indexer.conn.execute("UPDATE ledger_claims SET status='archived' WHERE claim_id='claim-1'")
        await indexer.conn.commit()
        assert await facade.search_cards(keyword="claim") == []
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_is_readable_forces_legacy_when_not_ready(tmp_path, monkeypatch):
    """Batch 3：readability = 配置走 Ledger 读 AND readiness 通过；
    未就绪 → False（调用方回退 legacy/拒绝），绝不静默读未就绪 Ledger。"""
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        # 配置要求走 Ledger 读
        monkeypatch.setattr("services.ledger_read_facade.config_manager.get", lambda key, default=None: "ledger" if key == "ledger.read_mode" else default)
        facade = LedgerReadFacade(indexer)
        # readiness 未通过（迁移未完成/对账未闭合）→ is_readable False，回退 legacy
        assert await facade.is_readable() is False
        # enabled 为 True（配置走 Ledger），但 readability 仍被 readiness 卡住
        assert facade.enabled is True
    finally:
        await indexer.close()
