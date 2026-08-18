"""Reconciliation 语义：derived/draft 无锚点卡不阻塞权威就绪；fail-closed 门禁。"""
from __future__ import annotations

import pytest

from core.config_manager import config_manager
from services.indexer import CardIndexer
from services.ledger_readiness import LedgerReadiness
from services.ledger_reconciliation import reconcile_legacy_cards


@pytest.mark.asyncio
async def test_reconciliation_derived_drafts_do_not_block(tmp_path, monkeypatch):
    from models.cards import InfoCard

    # 隔离全局 ledger 灰度：legacy 语义（authoritative=false）下验证 reconciliation。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        # 2 张 derived/draft 无锚点卡（派生草稿，不阻塞）
        await indexer.save_cards([
            InfoCard(card_id="d1", source_book_id="book", card_sub_type="note", content="derived one", status="draft"),
            InfoCard(card_id="d2", source_book_id="book", card_sub_type="note", content="derived two", evidence_level="derived"),
        ])
        # 1 张权威卡无锚点（真阻塞）
        await indexer.save_card(
            InfoCard(card_id="auth1", source_book_id="book", card_sub_type="note",
                     content="authoritative", status="approved", evidence_level="A")
        )
        report = await reconcile_legacy_cards(indexer)
        assert report["cards"] == 3
        assert report["derived_draft_count"] == 2
        assert set(report["derived_drafts"]) == {"d1", "d2"}
        assert report["missing_source"] == []
        assert report["missing_anchor"] == ["auth1"]
        assert report["missing_claim"] == []
        assert report["ready_for_authoritative"] is False
        # 补齐权威卡锚点后，ready 通过；derived 卡仍仅信息性记录
        await indexer.update_card_metadata("auth1", source_anchor={"source_document_id": "book", "paragraph": 1})
        report = await reconcile_legacy_cards(indexer)
        assert report["missing_anchor"] == []
        assert report["ready_for_authoritative"] is True
        assert report["derived_draft_count"] == 2
        assert set(report["derived_drafts"]) == {"d1", "d2"}
    finally:
        await indexer.close()


async def _ensure_migration_table(indexer: CardIndexer) -> None:
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


async def _insert_completed_migration(indexer: CardIndexer, run_id: str = "run-completed") -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await indexer.conn.execute(
        """INSERT INTO ledger_migration_runs(run_id, kind, dry_run, status, started_at, completed_at, total, migrated)
           VALUES (?, 'legacy_cards', 0, 'COMPLETED', ?, ?, 0, 0)""",
        (run_id, now, now),
    )
    await indexer.conn.commit()


@pytest.mark.asyncio
async def test_fail_closed_migration_not_completed(tmp_path):
    """迁移未完成（无 dry_run=0 COMPLETED run）→ migration_not_completed blocker。"""
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        report = await LedgerReadiness(indexer).report()
        assert report["ready_for_authoritative"] is False
        assert "migration_not_completed" in report["blockers"]
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_fail_closed_outbox_not_drained(tmp_path):
    """outbox 有 PENDING → outbox_not_drained blocker（迁移已完成仍阻塞）。"""
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await _ensure_migration_table(indexer)
        await _insert_completed_migration(indexer)
        await indexer.conn.execute(
            """INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at)
               VALUES ('evt-pending-1', 'card-pending', 'CARD_PROJECTED', '{}', 'PENDING', '2026-01-01T00:00:00+00:00')"""
        )
        await indexer.conn.commit()
        report = await LedgerReadiness(indexer).report()
        assert report["ready_for_authoritative"] is False
        assert "outbox_not_drained" in report["blockers"]
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_fail_closed_card_claim_count_mismatch(tmp_path, monkeypatch):
    """card_count != claim_count → card_claim_count_mismatch blocker。"""
    from models.cards import InfoCard

    # 隔离全局 ledger 灰度：legacy 语义（authoritative=false）下该计数检查仍生效。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await _ensure_migration_table(indexer)
        await _insert_completed_migration(indexer)
        await indexer.save_card(
            InfoCard(source_book_id="book", card_sub_type="note", content="legacy card", status="approved")
        )
        # 制造 1 张卡无对应 claim 的不一致
        await indexer.conn.execute("DELETE FROM ledger_claims")
        await indexer.conn.commit()
        report = await LedgerReadiness(indexer).report()
        assert report["ready_for_authoritative"] is False
        assert any(b.startswith("card_claim_count_mismatch") for b in report["blockers"])
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_fail_closed_dual_read_mismatch(tmp_path, monkeypatch):
    """dual_read_enabled=true 且 dual 有 mismatch → dual_read_mismatch blocker。"""
    monkeypatch.setattr(config_manager, "get_bool", lambda key, default=False: key == "ledger.dual_read_enabled")

    class _FakeDualRead:
        def report(self):
            return {"mode": "legacy", "mismatch_count": 2, "mismatches": [{"query_hash": "q1"}, {"query_hash": "q2"}], "ready": False}

    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        report = await LedgerReadiness(indexer, dual_read=_FakeDualRead()).report()
        assert report["ready_for_authoritative"] is False
        assert "dual_read_mismatch" in report["blockers"]
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_dual_read_not_blocking_in_authoritative_frozen(tmp_path, monkeypatch):
    """authoritative + 关闭 legacy 投影：cards 投影已冻结，双读对比不适用，不应作阻塞。"""
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", True)
    monkeypatch.setitem(ledger_cfg, "allow_legacy_card_projection", False)
    monkeypatch.setitem(ledger_cfg, "dual_read_enabled", True)
    monkeypatch.setitem(ledger_cfg, "read_mode", "ledger")

    class _FakeDualRead:
        def report(self):
            return {"mode": "ledger", "mismatch_count": 3, "ready": False}

    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await _ensure_migration_table(indexer)
        await _insert_completed_migration(indexer)
        report = await LedgerReadiness(indexer, dual_read=_FakeDualRead()).report()
        assert "dual_read_mismatch" not in report["blockers"]
        assert report["dual_read"]["status"] == "blocked"  # 语义仍上报 blocked，但不阻塞 ready
    finally:
        await indexer.close()
