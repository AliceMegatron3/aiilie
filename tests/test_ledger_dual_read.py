"""Ledger dual-read shadow comparison tests."""
from __future__ import annotations

import pytest

from core.config_manager import config_manager
from services.indexer import CardIndexer
from services.ledger_dual_read import LedgerDualRead
from services.ledger_read_facade import LedgerReadFacade


@pytest.mark.asyncio
async def test_dual_read_records_missing_projection(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        dual = LedgerDualRead(indexer, LedgerReadFacade(indexer))
        result = await dual.compare_search(keyword="nothing")
        assert result["comparison"]["equal"] is True
        assert dual.report()["ready"] is True
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_dual_read_set_equal_ignores_order(tmp_path, monkeypatch):
    """集合一致但顺序不同（legacy rowid DESC vs ledger created_at DESC）不应判为 mismatch。"""
    from models.cards import InfoCard

    # 隔离全局 ledger 灰度：dual-read 需要 legacy cards 表与 ledger_claims 双写。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await indexer.save_cards([
            InfoCard(card_id="c1", source_book_id="book", card_sub_type="note", content="one"),
            InfoCard(card_id="c2", source_book_id="book", card_sub_type="note", content="two"),
            InfoCard(card_id="c3", source_book_id="book", card_sub_type="note", content="three"),
        ])
        # 统一 claim created_at，使 facade 仅按 claim_id ASC 排序（c1,c2,c3），
        # 与 legacy rowid DESC（c3,c2,c1）确定性产生顺序差异，但集合一致。
        await indexer.conn.execute("UPDATE ledger_claims SET created_at='2026-01-01T00:00:00+00:00'")
        await indexer.conn.commit()
        dual = LedgerDualRead(indexer, LedgerReadFacade(indexer))
        result = await dual.compare_search(source_book="book", limit=50)
        comparison = result["comparison"]
        assert comparison["missing_in_ledger"] == []
        assert comparison["missing_in_legacy"] == []
        assert comparison["order_mismatch"] is True
        assert comparison["equal"] is True
        assert dual.report()["mismatch_count"] == 0
        assert dual.report()["ready"] is True
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_dual_read_set_missing_in_ledger_not_equal(tmp_path, monkeypatch):
    """legacy 有而 ledger 无的漂移应判为 not equal 并进入 mismatch。"""
    from models.cards import InfoCard

    # 隔离全局 ledger 灰度：dual-read 需要 legacy cards 表与 ledger_claims 双写。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await indexer.save_cards([
            InfoCard(card_id="c1", source_book_id="book", card_sub_type="note", content="one"),
            InfoCard(card_id="c2", source_book_id="book", card_sub_type="note", content="two"),
            InfoCard(card_id="c3", source_book_id="book", card_sub_type="note", content="three"),
        ])
        # 模拟 legacy 有、ledger 无的漂移：仅删除 ledger_claims 中的 c1（不物理删卡）。
        await indexer.conn.execute("DELETE FROM ledger_claims WHERE claim_id='c1'")
        await indexer.conn.commit()
        dual = LedgerDualRead(indexer, LedgerReadFacade(indexer))
        result = await dual.compare_search(source_book="book", limit=50)
        comparison = result["comparison"]
        assert comparison["missing_in_ledger"] == ["c1"]
        assert comparison["equal"] is False
        assert dual.report()["mismatch_count"] == 1
        assert dual.report()["ready"] is False
    finally:
        await indexer.close()
