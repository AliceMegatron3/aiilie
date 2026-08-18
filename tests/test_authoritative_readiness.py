"""Real-data readiness fixture documenting the legacy-source blocker."""
from __future__ import annotations

import pytest

from core.config_manager import config_manager
from services.indexer import CardIndexer
from services.ledger_readiness import LedgerReadiness


@pytest.mark.asyncio
async def test_readiness_blocks_card_claim_count_mismatch(tmp_path, monkeypatch):
    # 隔离全局 ledger 灰度：本用例验证 legacy 语义（authoritative=false）下写入
    # 无来源锚点卡片后 readiness 被阻断，不应受 config.yaml 中
    # ledger.authoritative=true 影响；monkeypatch 在用例结束后自动还原配置。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        from models.cards import InfoCard
        await indexer.save_card(InfoCard(source_book_id="book", card_sub_type="note", content="legacy"))
        report = await LedgerReadiness(indexer).report()
        assert report["ready_for_authoritative"] is False
        assert "migration_not_completed" in report["blockers"]
    finally:
        await indexer.close()


async def _ensure_migration_table(indexer) -> None:
    """测试用：确保 ledger_migration_runs 表存在（由 migrate_legacy_cards 惰性创建）。"""
    await indexer.conn.execute(
        """CREATE TABLE IF NOT EXISTS ledger_migration_runs (
            run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, dry_run INTEGER NOT NULL,
            status TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0, migrated INTEGER NOT NULL DEFAULT 0,
            previewed INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0,
            errors TEXT NOT NULL DEFAULT '[]', started_at TEXT NOT NULL, completed_at TEXT,
            source_high_water INTEGER NOT NULL DEFAULT 0, last_rowid INTEGER NOT NULL DEFAULT 0,
            last_card_id TEXT NOT NULL DEFAULT '', source_snapshot_hash TEXT NOT NULL DEFAULT '',
            manifest TEXT NOT NULL DEFAULT '{}', checkpoint_at TEXT
        )"""
    )
    await indexer.conn.commit()


async def _insert_completed_migration(indexer, run_id: str = "run-completed") -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await indexer.conn.execute(
        """INSERT INTO ledger_migration_runs(run_id, kind, dry_run, status, started_at, completed_at, total, migrated)
           VALUES (?, 'legacy_cards', 0, 'COMPLETED', ?, ?, 0, 0)""",
        (run_id, now, now),
    )
    await indexer.conn.commit()


@pytest.mark.asyncio
async def test_readiness_skips_card_claim_count_check_when_projection_off(tmp_path, monkeypatch):
    """authoritative + 关闭 legacy 投影：cards 表冻结为只读投影，
    cards 数 != claims 数不再阻塞 readiness（card_claim_count_check=skipped）。"""
    from models.cards import InfoCard, SourceAnchor

    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", True)
    monkeypatch.setitem(ledger_cfg, "allow_legacy_card_projection", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "ledger")
    monkeypatch.setitem(ledger_cfg, "dual_read_enabled", False)
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await _ensure_migration_table(indexer)
        await _insert_completed_migration(indexer)
        # 只写 Ledger：cards 表 0 行、ledger_claims 1 行 → 计数天然不一致
        await indexer.save_card(
            InfoCard(
                card_id="skip-count-1", source_book_id="book", source_document_id="book",
                source_anchor=SourceAnchor(source_document_id="book", paragraph=1),
                card_sub_type="note", content="ledger-only",
            )
        )
        # 排空 outbox，使唯一可能的 blocker 是计数不一致
        await indexer.conn.execute("UPDATE ledger_outbox SET status='APPLIED'")
        await indexer.conn.commit()
        report = await LedgerReadiness(indexer).report()
        assert report["card_claim_count_check"] == "skipped"
        assert not any(b.startswith("card_claim_count_mismatch") for b in report["blockers"])
        assert report["ready_for_authoritative"] is True
    finally:
        await indexer.close()
