"""
tests/test_card_indexer.py — CardIndexer 单元测试
=================================================
覆盖：卡片保存/检索（多维过滤）/详情读取/摘要批量读取/删除。
通过 isolated_paths 夹具将索引库与冷存储目录重定向到临时目录。
"""
from __future__ import annotations

import pytest

from models.cards import DataCard, InfoCard
from services.indexer import CardIndexer

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def indexer(isolated_paths, monkeypatch):
    # 隔离全局 ledger 灰度：本文件验证 legacy 卡片存储语义（authoritative=false），
    # 不受 config.yaml 中 ledger.authoritative=true 影响；monkeypatch 自动还原。
    from core.config_manager import config_manager
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    idx = CardIndexer(isolated_paths / "index")
    await idx.initialize()
    yield idx
    await idx.close()


async def test_save_and_search_info_card(indexer):
    card = InfoCard(
        source_book_id="book1",
        content="主角是一名剑客",
        tags=["人设", "主角"],
        card_sub_type="人物性格",
        category="character",
        original_fragment="他握紧了剑。",
    )
    await indexer.save_card(card)

    results = await indexer.search_cards(card_type="info", source_book="book1")
    assert len(results) == 1
    assert results[0]["category"] == "character"
    assert "人设" in results[0]["tags"]


async def test_search_by_keyword_and_limits(indexer):
    for i in range(5):
        await indexer.save_card(InfoCard(
            source_book_id="book2",
            content=f"关键字内容 {i}",
            tags=["通用"],
            card_sub_type="世界观",
        ))
    results = await indexer.search_cards(keyword="关键字", limit=3)
    assert len(results) == 3
    total = await indexer.search_cards(keyword="关键字", limit=100)
    assert len(total) == 5


async def test_data_card_roundtrip(indexer):
    card = DataCard(
        source_book_id="book3",
        content="节奏曲线数据",
        metric_type="progression_pacing",
        value={"chapter": 10, "tempo": "fast"},
    )
    await indexer.save_card(card)
    results = await indexer.search_cards(card_type="data", source_book="book3")
    assert len(results) == 1
    detail = await indexer.get_card_detail(card.card_id)
    assert detail is not None
    assert detail["card_type"] == "data"


async def test_get_card_summaries(indexer):
    for i in range(3):
        await indexer.save_card(InfoCard(
            source_book_id="book4",
            content=f"摘要{i}",
            tags=[],
            card_sub_type="logic_general",
        ))
    summaries = await indexer.get_card_summaries("book4", limit=2)
    assert len(summaries) == 2
    # 逻辑子类型存储在 subtype 列（card_type 列是大类 info/data）
    filtered = await indexer.get_card_summaries("book4", subtype_like="%logic%")
    assert len(filtered) == 3


async def test_delete_card(indexer):
    card = InfoCard(source_book_id="book5", content="将被删除", tags=[], card_sub_type="世界观")
    await indexer.save_card(card)
    assert await indexer.delete_card(card.card_id) is True
    assert await indexer.get_card_detail(card.card_id) is None
