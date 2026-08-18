"""
tests/test_ledger_backfill.py — 旧 cards 冷数据回填 + 投影 fingerprint 调整测试
==============================================================================
覆盖：
- backfill_legacy_cards_from_cold：旧 schema 补列、冷数据回填、幂等、dry_run 不写库、
  source_book 过滤、孤儿冷文件计为 skipped_files；
- 回填后 migrate_legacy_cards：2 张 content 完全相同的旧卡都能写入 ledger_claims
  （fingerprint 含 card_id，不再触发 UNIQUE 冲突）。

所有数据库操作均基于 tmp_path，不触碰真实 AppData 库。
"""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from services.indexer import CardIndexer
from services.ledger_migration import migrate_legacy_cards
from services.ledger_source_registration import backfill_legacy_cards_from_cold

pytestmark = pytest.mark.asyncio

# 与真实旧库 cards 表一致的 schema：没有 content/source_chapter/detail_path/create_time。
_OLD_SCHEMA_COLUMNS = """
    card_id TEXT PRIMARY KEY, card_type TEXT NOT NULL, subtype TEXT NOT NULL DEFAULT '',
    source_book TEXT NOT NULL, summary TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
    file_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT '',
    weight REAL NOT NULL DEFAULT 1.0, category TEXT NOT NULL DEFAULT 'misc',
    entropy_score REAL NOT NULL DEFAULT 0.5, utility_score REAL NOT NULL DEFAULT 0.5,
    library_id TEXT NOT NULL DEFAULT '', knowledge_type TEXT NOT NULL DEFAULT 'FACT',
    book_type_ids TEXT NOT NULL DEFAULT '[]', domain TEXT NOT NULL DEFAULT '',
    scope_level TEXT NOT NULL DEFAULT 'book', status TEXT NOT NULL DEFAULT 'draft',
    evidence_level TEXT NOT NULL DEFAULT 'unknown', rule_strength TEXT NOT NULL DEFAULT 'none',
    source_document_id TEXT NOT NULL DEFAULT '', source_anchor TEXT NOT NULL DEFAULT '{}',
    valid_time_start TEXT, valid_time_end TEXT, valid_places TEXT NOT NULL DEFAULT '[]',
    related_card_ids TEXT NOT NULL DEFAULT '[]'
"""


async def _build_old_schema_indexer(tmp_path: Path) -> CardIndexer:
    """先用旧 schema 预建 cards 表，再让 CardIndexer.initialize() 叠加其余 ledger 表。"""
    db_path = tmp_path / "index" / "library_index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.execute(f"CREATE TABLE cards ({_OLD_SCHEMA_COLUMNS})")
    await conn.commit()
    await conn.close()
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    return indexer


async def _insert_old_row(
    indexer: CardIndexer,
    card_id: str,
    source_book: str,
    summary: str,
    status: str = "draft",
    evidence_level: str = "unknown",
) -> None:
    """按旧 schema 直插热行（不触发 save_card 的 ledger 投影）。"""
    await indexer.conn.execute(
        """INSERT INTO cards(card_id, source_book, card_type, subtype, category, summary,
                             tags, created_at, status, evidence_level)
           VALUES (?, ?, 'info', 'note', 'misc', ?, '[]', '2026-01-01T00:00:00+00:00', ?, ?)""",
        (card_id, source_book, summary, status, evidence_level),
    )
    await indexer.conn.commit()


async def _card_row(indexer: CardIndexer, card_id: str) -> aiosqlite.Row | None:
    cursor = await indexer.conn.execute(
        "SELECT content, source_chapter, detail_path, create_time, summary FROM cards WHERE card_id = ?",
        (card_id,),
    )
    return await cursor.fetchone()


async def _table_columns(indexer: CardIndexer, table: str) -> set[str]:
    cursor = await indexer.conn.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in await cursor.fetchall()}


async def _count(indexer: CardIndexer, table: str) -> int:
    cursor = await indexer.conn.execute(f"SELECT COUNT(*) FROM {table}")
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def test_backfill_fills_legacy_columns_idempotently(tmp_path):
    indexer = await _build_old_schema_indexer(tmp_path)
    try:
        assert "content" not in await _table_columns(indexer, "cards")
        await _insert_old_row(indexer, "cold-1", "book_legacy", "根据默认规则提炼的模板摘要")
        await _insert_old_row(indexer, "cold-2", "book_legacy", "根据默认规则提炼的模板摘要")
        await _insert_old_row(indexer, "cold-4", "book_legacy", "作为 content 兜底的模板摘要")

        cold_root = tmp_path / "cold"
        (cold_root / "data_misc").mkdir(parents=True)
        # cold-1：original_fragment（≥20字）应优先于 content
        (cold_root / "data_misc" / "cold-1.card").write_text(
            json.dumps({
                "card_id": "cold-1", "source_book_id": "book_legacy",
                "source_chapter": "第311章", "content": "短内容",
                "create_time": "2026-01-01T00:00:00+00:00",
                "original_fragment": "萧炎眼神凝重地望向远处的魔兽山脉，缓缓握紧了手中的玄重尺。",
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        # cold-2：无 original_fragment，用冷 content
        (cold_root / "data_misc" / "cold-2.card").write_text(
            json.dumps({
                "card_id": "cold-2", "source_book_id": "book_legacy",
                "source_chapter": "Part-945", "content": "冷存储的真实内容",
                "create_time": "2026-01-02T00:00:00+00:00",
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        # cold-4：无 original_fragment 且无 content，回退到热 summary
        (cold_root / "data_misc" / "cold-4.card").write_text(
            json.dumps({
                "card_id": "cold-4", "source_book_id": "book_legacy",
                "source_chapter": "第1章", "create_time": "",
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        # 孤儿冷文件（无热行）→ skipped_files
        (cold_root / "data_misc" / "cold-3.card").write_text(
            json.dumps({"card_id": "cold-3", "content": "孤儿卡"}, ensure_ascii=False),
            encoding="utf-8",
        )
        # .ai_backup 文件必须跳过
        (cold_root / "data_misc" / "cold-1.card.ai_backup").write_text(
            json.dumps({"card_id": "cold-1", "content": "备份内容不应写入", "source_chapter": "备份章"}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = await backfill_legacy_cards_from_cold(indexer, [cold_root])
        assert result["dry_run"] is False
        assert result["total_cards"] == 3
        assert result["updated_cards"] == 3
        assert result["skipped_files"] == 1
        assert result["errors"] == []

        # 缺失列已幂等补齐
        cols = await _table_columns(indexer, "cards")
        for col in ("content", "source_chapter", "detail_path", "create_time"):
            assert col in cols

        row = await _card_row(indexer, "cold-1")
        assert row["content"] == "萧炎眼神凝重地望向远处的魔兽山脉，缓缓握紧了手中的玄重尺。"
        assert row["source_chapter"] == "第311章"
        assert row["detail_path"].endswith("cold-1.card")
        assert row["create_time"] == "2026-01-01T00:00:00+00:00"
        row = await _card_row(indexer, "cold-2")
        assert row["content"] == "冷存储的真实内容"
        assert row["source_chapter"] == "Part-945"
        assert row["create_time"] == "2026-01-02T00:00:00+00:00"
        row = await _card_row(indexer, "cold-4")
        assert row["content"] == "作为 content 兜底的模板摘要"
        assert row["source_chapter"] == "第1章"

        # 幂等：二次调用不破坏数据，updated_cards 归零
        again = await backfill_legacy_cards_from_cold(indexer, [cold_root])
        assert again["total_cards"] == 3
        assert again["updated_cards"] == 0
        row = await _card_row(indexer, "cold-1")
        assert row["content"] == "萧炎眼神凝重地望向远处的魔兽山脉，缓缓握紧了手中的玄重尺。"
        assert row["source_chapter"] == "第311章"
    finally:
        await indexer.close()


async def test_backfill_dry_run_writes_nothing(tmp_path):
    indexer = await _build_old_schema_indexer(tmp_path)
    try:
        await _insert_old_row(indexer, "dry-1", "book_dry", "模板摘要")
        cold_root = tmp_path / "cold"
        (cold_root / "data_misc").mkdir(parents=True)
        (cold_root / "data_misc" / "dry-1.card").write_text(
            json.dumps({
                "card_id": "dry-1", "source_book_id": "book_dry",
                "source_chapter": "第1章", "content": "计划写入的内容",
                "create_time": "2026-03-03T00:00:00+00:00",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        result = await backfill_legacy_cards_from_cold(indexer, [cold_root], dry_run=True)
        assert result["dry_run"] is True
        assert result["total_cards"] == 1
        assert result["updated_cards"] == 1
        # 未写库：schema 未改变，仍无 content/source_chapter 列
        cols = await _table_columns(indexer, "cards")
        for col in ("content", "source_chapter", "detail_path", "create_time"):
            assert col not in cols
    finally:
        await indexer.close()


async def test_backfill_respects_source_book_filter(tmp_path):
    indexer = await _build_old_schema_indexer(tmp_path)
    try:
        await _insert_old_row(indexer, "bk-a", "book_a", "摘要A")
        await _insert_old_row(indexer, "bk-b", "book_b", "摘要B")
        cold_root = tmp_path / "cold"
        (cold_root / "info_misc").mkdir(parents=True)
        for cid, book in (("bk-a", "book_a"), ("bk-b", "book_b")):
            (cold_root / "info_misc" / f"{cid}.card").write_text(
                json.dumps({
                    "card_id": cid, "source_book_id": book, "source_chapter": "第1章", "content": "真实内容",
                }, ensure_ascii=False),
                encoding="utf-8",
            )

        result = await backfill_legacy_cards_from_cold(indexer, [cold_root], source_book="book_a")
        assert result["total_cards"] == 1
        assert result["updated_cards"] == 1
        assert result["skipped_files"] == 1  # bk-b 被过滤
        assert (await _card_row(indexer, "bk-a"))["content"] == "真实内容"
        assert (await _card_row(indexer, "bk-b"))["content"] == ""
    finally:
        await indexer.close()


async def test_backfill_then_migration_no_fingerprint_collision(tmp_path):
    indexer = await _build_old_schema_indexer(tmp_path)
    try:
        # 2 张 content 完全相同的旧卡（旧 schema 无 content 列 → 热行 content=''）
        await _insert_old_row(indexer, "fp-a", "book_fp", "模板摘要A")
        await _insert_old_row(indexer, "fp-b", "book_fp", "模板摘要B")
        cold_root = tmp_path / "cold"
        (cold_root / "data_misc").mkdir(parents=True)
        for cid in ("fp-a", "fp-b"):
            (cold_root / "data_misc" / f"{cid}.card").write_text(
                json.dumps({
                    "card_id": cid, "source_book_id": "book_fp",
                    "source_chapter": "第1章", "content": "两张卡内容完全相同，用于触发指纹冲突",
                    "create_time": "2026-01-01T00:00:00+00:00",
                }, ensure_ascii=False),
                encoding="utf-8",
            )

        backfill = await backfill_legacy_cards_from_cold(indexer, [cold_root])
        assert backfill["total_cards"] == 2
        assert backfill["errors"] == []

        migrated = await migrate_legacy_cards(indexer, dry_run=False)
        assert migrated["status"] == "COMPLETED"
        assert migrated["migrated"] == 2
        assert migrated["errors"] == []
        assert await _count(indexer, "ledger_claims") == 2

        # fingerprint 因含 card_id 而各不相同（否则 content 相同必冲突）
        cursor = await indexer.conn.execute(
            "SELECT claim_id, fingerprint FROM ledger_claims ORDER BY claim_id"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[0]["fingerprint"] != rows[1]["fingerprint"]
        for row in rows:
            assert len(row["fingerprint"]) == 64
    finally:
        await indexer.close()
