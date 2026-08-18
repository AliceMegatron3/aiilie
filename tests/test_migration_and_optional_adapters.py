"""旧卡片迁移、可选适配器和技能候选兼容测试。"""
from __future__ import annotations

import pytest
from io import BytesIO

from core.config_manager import config_manager
from models.cards import InfoCard
from services.indexer import CardIndexer
from services.ledger_migration import migrate_legacy_cards
from services.parser import ParserFactory
from services.simpy_adapter import SimpyLawAdapter, SimpyUnavailableError


@pytest.mark.asyncio
async def test_legacy_card_migration_supports_dry_run(tmp_path, monkeypatch):
    # 隔离全局 ledger 灰度：legacy 迁移用例在非权威模式下写入（authoritative=false），
    # 不受 config.yaml 中 ledger.authoritative=true 影响。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await indexer.save_card(InfoCard(source_book_id="book", content="legacy", card_sub_type="note"))
        preview = await migrate_legacy_cards(indexer, dry_run=True)
        assert preview["dry_run"] is True
        assert preview["migrated"] == 0
        assert preview["previewed"] == 1
        applied = await migrate_legacy_cards(indexer, dry_run=False)
        assert applied["migrated"] == 1
        assert applied["run_id"]
        runs = await indexer.conn.execute("SELECT status FROM ledger_migration_runs WHERE run_id = ?", (applied["run_id"],))
        assert (await runs.fetchone())[0] == "COMPLETED"
    finally:
        await indexer.close()


def test_optional_parser_is_explicitly_selectable():
    parser = ParserFactory.get_parser("book.pdf")
    assert parser.parser_id in {"pypdf", "docling"}


def test_simpy_adapter_reports_optional_dependency():
    adapter = SimpyLawAdapter()
    if not adapter.available():
        with pytest.raises(SimpyUnavailableError):
            adapter.run_processes([], 10)
        with pytest.raises(SimpyUnavailableError):
            adapter.run_resource_processes([], capacity=2, until=10)
