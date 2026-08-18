"""Checkpointed migration and reconciliation tests."""
from __future__ import annotations

import pytest

from core.config_manager import config_manager
from models.cards import InfoCard
from services.indexer import CardIndexer
from services.ledger_migration import migrate_legacy_cards


async def _insert_raw_card(indexer: CardIndexer, card_id: str, book_id: str, content: str) -> None:
    """直接写入热索引，绕过 save_card 的 ledger 投影，模拟历史遗留卡片。"""
    await indexer.conn.execute(
        """INSERT INTO cards(card_id, source_book, card_type, category, subtype, summary, content, create_time, detail_path)
           VALUES (?, ?, 'info', 'misc', 'note', ?, ?, ?, ?)""",
        (card_id, book_id, content, content, "2026-01-01T00:00:00+00:00", f"/tmp/{card_id}.json"),
    )
    await indexer.conn.commit()


async def _count_ledger_claims(indexer: CardIndexer) -> int:
    cursor = await indexer.conn.execute("SELECT COUNT(*) FROM ledger_claims")
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


@pytest.mark.asyncio
async def test_migration_records_high_water_and_manifest(tmp_path, monkeypatch):
    # 隔离全局 ledger 灰度：迁移用例按 legacy 语义（authoritative=false）写入。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await indexer.save_card(InfoCard(source_book_id="book", content="legacy", card_sub_type="note"))
        result = await migrate_legacy_cards(indexer, dry_run=True)
        assert result["status"] == "PREVIEW"
        assert result["manifest"]
        cursor = await indexer.conn.execute(
            "SELECT source_high_water, source_snapshot_hash, manifest FROM ledger_migration_runs WHERE run_id=?",
            (result["run_id"],),
        )
        row = await cursor.fetchone()
        assert row[0] > 0
        assert row[1]
        assert "card_id" in row[2]
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_dry_run_preview_reports_zero_migrated_and_writes_nothing(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        for i in range(3):
            await _insert_raw_card(indexer, f"legacy-preview-{i}", "book-preview", f"legacy content {i}")
        assert await _count_ledger_claims(indexer) == 0

        preview = await migrate_legacy_cards(indexer, dry_run=True)
        assert preview["status"] == "PREVIEW"
        assert preview["dry_run"] is True
        assert preview["total"] == 3
        assert preview["migrated"] == 0
        assert preview["previewed"] == 3
        assert preview["skipped"] == 0
        assert preview["errors"] == []
        assert len(preview["manifest"]) == 3
        assert all(item["operation"] == "preview" for item in preview["manifest"])
        assert all(item["applied"] is False for item in preview["manifest"])
        assert await _count_ledger_claims(indexer) == 0
        cursor = await indexer.conn.execute(
            "SELECT migrated, previewed FROM ledger_migration_runs WHERE run_id=?", (preview["run_id"],)
        )
        row = await cursor.fetchone()
        assert row is not None and row[0] == 0 and row[1] == 3

        applied = await migrate_legacy_cards(indexer, dry_run=False)
        assert applied["status"] == "COMPLETED"
        assert applied["migrated"] == 3
        assert applied["previewed"] == 0
        assert all(item["operation"] == "project" for item in applied["manifest"])
        assert await _count_ledger_claims(indexer) == 3
    finally:
        await indexer.close()
