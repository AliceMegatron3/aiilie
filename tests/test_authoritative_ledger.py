"""Ledger 权威切换与旧技能迁移入口测试。"""
from __future__ import annotations

import pytest

from models.cards import InfoCard
from services.indexer import CardIndexer
from core.config_manager import config_manager


def _patch_ledger_config(monkeypatch, **overrides) -> None:
    """在用例内隔离全局 ledger 灰度配置（测试结束自动还原）。"""
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", True)
    monkeypatch.setitem(ledger_cfg, "read_mode", "ledger")
    for key, value in overrides.items():
        monkeypatch.setitem(ledger_cfg, key, value)


@pytest.mark.asyncio
async def test_authoritative_ledger_rejects_missing_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "get_bool", lambda key, default=False: key == "ledger.authoritative")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        with pytest.raises(ValueError, match="来源锚点"):
            await indexer.save_card(InfoCard(source_book_id="book", content="no anchor", card_sub_type="note"))
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_authoritative_no_projection_writes_ledger_only(tmp_path, monkeypatch):
    """authoritative + 关闭 legacy 投影：只写 Ledger（claims+outbox），不写旧 cards 表。"""
    from models.cards import SourceAnchor

    _patch_ledger_config(monkeypatch, allow_legacy_card_projection=False)
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        card = InfoCard(
            card_id="ledger-only-1",
            source_book_id="book",
            source_document_id="book",
            source_anchor=SourceAnchor(source_document_id="book", paragraph=1, quote="q"),
            card_sub_type="note",
            content="ledger-only content",
        )
        saved = await indexer.save_card(card)
        assert saved == "ledger-only-1"  # 成功返回，无 raise

        # cards 表未新增行（冻结为只读投影）
        cursor = await indexer.conn.execute("SELECT COUNT(*) FROM cards WHERE card_id=?", ("ledger-only-1",))
        assert (await cursor.fetchone())[0] == 0
        # ledger_claims 增加
        cursor = await indexer.conn.execute("SELECT COUNT(*) FROM ledger_claims WHERE claim_id=?", ("ledger-only-1",))
        assert (await cursor.fetchone())[0] == 1
        # outbox 有对应 CARD_PROJECTED 事件
        cursor = await indexer.conn.execute(
            "SELECT status FROM ledger_outbox WHERE event_id=?", ("card_projected:ledger-only-1",)
        )
        row = await cursor.fetchone()
        assert row is not None and row["status"] in ("PENDING", "APPLIED")
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_authoritative_legacy_projection_still_double_writes(tmp_path, monkeypatch):
    """authoritative + 开启 legacy 投影：维持兼容双写（cards 表 + Ledger）。"""
    from models.cards import SourceAnchor

    _patch_ledger_config(monkeypatch, allow_legacy_card_projection=True)
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        card = InfoCard(
            card_id="legacy-proj-1",
            source_book_id="book",
            source_document_id="book",
            source_anchor=SourceAnchor(source_document_id="book", paragraph=1),
            card_sub_type="note",
            content="compat double write",
        )
        saved = await indexer.save_card(card)
        assert saved == "legacy-proj-1"
        cursor = await indexer.conn.execute("SELECT COUNT(*) FROM cards WHERE card_id=?", ("legacy-proj-1",))
        assert (await cursor.fetchone())[0] == 1
        cursor = await indexer.conn.execute("SELECT COUNT(*) FROM ledger_claims WHERE claim_id=?", ("legacy-proj-1",))
        assert (await cursor.fetchone())[0] == 1
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_authoritative_facade_read_delete_consistent(tmp_path, monkeypatch):
    """authoritative + 关闭 legacy 投影：读/删走 Ledger facade，且不手工改库。

    验证 cards 表为空投影时，detail/delete 仍基于 ledger_claims 保持一致：
    写入→可读、删除→archived→不可读、删不存在的返回 False。
    """
    from models.cards import SourceAnchor
    from services.ledger_read_facade import LedgerReadFacade

    _patch_ledger_config(monkeypatch, allow_legacy_card_projection=False)
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        facade = LedgerReadFacade(indexer=indexer)
        assert facade.enabled is True

        card = InfoCard(
            card_id="facade-crud-1",
            source_book_id="book",
            source_document_id="book",
            source_anchor=SourceAnchor(source_document_id="book", paragraph=1, quote="q"),
            card_sub_type="note",
            content="ledger crud content",
        )
        await indexer.save_card(card)

        # 读：authoritative 下详情来自 ledger_claims
        detail = await facade.get_card_detail("facade-crud-1")
        assert detail is not None
        assert detail["card_id"] == "facade-crud-1"

        # 删：归档 claim，返回 True 且随后不可读
        assert await facade.delete_card("facade-crud-1") is True
        assert await facade.get_card_detail("facade-crud-1") is None

        # 删不存在的：返回 False（不误报成功）
        assert await facade.delete_card("does-not-exist") is False
    finally:
        await indexer.close()
