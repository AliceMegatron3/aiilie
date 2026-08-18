"""统一 Ledger、FTS5 投影和法则编译器回归测试。"""
from __future__ import annotations

import pytest

from core.config_manager import config_manager
from models.cards import DataCard, InfoCard
from models.ledger import LawRecord
from services.indexer import CardIndexer
from services.law_compiler import LawCompiler


@pytest.mark.asyncio
async def test_card_projects_to_ledger_and_fts(tmp_path, monkeypatch):
    # 隔离全局 ledger 灰度：legacy 语义（authoritative=false）下验证投影与 FTS。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        card = InfoCard(
            source_book_id="book-ledger",
            source_document_id="book-ledger",
            content="灵气浓度影响作物产量",
            source_chapter="第一章",
            card_sub_type="world_view",
            original_fragment="田地边缘的灵气逐渐变浓。",
        )
        await indexer.save_card(card)
        cursor = await indexer.conn.execute(
            "SELECT document_id, passage_id FROM ledger_evidence WHERE evidence_id = ?",
            (f"evidence_{card.card_id}",),
        )
        row = await cursor.fetchone()
        assert row[0] == "book-ledger"
        assert row[1] == f"passage_{card.card_id}"
        assert await indexer.count_search_cards(keyword="灵气浓度") == 1
    finally:
        await indexer.close()


def test_law_compiler_preserves_author_override_without_calling_it_reality():
    law = LawRecord(
        law_id="qi-ecology",
        layer="AUTHOR_CANON",
        name="灵气生态派生",
        variables={"qi_density": {"value": 3, "unit": "dimensionless"}},
        strength="hard",
        status="draft",
    )
    report = LawCompiler().validate(law)
    assert report["valid"] is True
    assert report["layer"] == "AUTHOR_CANON"


def test_law_compiler_rejects_unbound_active_reality_base():
    law = LawRecord(
        law_id="gravity",
        layer="REALITY_BASE",
        name="gravity",
        variables={"g": {"value": 9.8, "unit": "meter / second ** 2"}},
        strength="hard",
        status="active",
    )
    report = LawCompiler().validate(law)
    assert report["valid"] is False
    assert any("source_evidence" in item for item in report["errors"])
