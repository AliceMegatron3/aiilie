"""旧卡片到 Ledger 的可回滚迁移工具。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import asyncio
import hashlib
import json
from uuid import uuid4

from services.indexer import CardIndexer


async def list_migration_runs(indexer: CardIndexer, limit: int = 50) -> list[dict[str, Any]]:
    try:
        cursor = await indexer.conn.execute(
            "SELECT * FROM ledger_migration_runs ORDER BY started_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        )
        return [dict(row) for row in await cursor.fetchall()]
    except Exception:
        return []


# 进程内迁移互斥锁：防止同一进程内并发迁移互相覆盖 (阶段C 并发一致性)。
_MIGRATION_LOCK = None


async def migrate_legacy_cards(indexer: CardIndexer, dry_run: bool = False) -> dict[str, Any]:
    """把旧 cards 热索引补投影到 Ledger，不删除旧卡片。

    包装层负责并发互斥：进程内 asyncio.Lock 串行化，DB 层再校验「同一时刻仅允许
    一个非 dry-run 迁移运行」。
    """
    global _MIGRATION_LOCK
    if _MIGRATION_LOCK is None:
        _MIGRATION_LOCK = asyncio.Lock()
    async with _MIGRATION_LOCK:
        return await _migrate_legacy_cards_locked(indexer, dry_run=dry_run)


async def _migrate_legacy_cards_locked(indexer: CardIndexer, dry_run: bool = False) -> dict[str, Any]:
    """把旧 cards 热索引补投影到 Ledger，不删除旧卡片。

    迁移记录只描述本次尝试；失败的卡片会单独报告，调用方可重复执行。
    """
    if not dry_run:
        # 阶段C：仅允许一个非 dry-run 迁移同时进行，防止跨进程并发写 ledger 竞态。
        # 首次运行/全新库可能尚无 ledger_migration_runs 表，按「无进行中」处理。
        try:
            active = await indexer.conn.execute(
                "SELECT run_id FROM ledger_migration_runs WHERE kind='legacy_cards' AND dry_run=0 AND status='RUNNING' LIMIT 1"
            )
            active_row = await active.fetchone()
        except Exception:
            active_row = None
        if active_row is not None:
            return {
                "run_id": "",
                "dry_run": dry_run,
                "status": "CONFLICT",
                "message": f"已有进行中的迁移运行 {active_row[0]}，拒绝并发执行",
                "total": 0,
                "migrated": 0,
                "previewed": 0,
                "skipped": 0,
                "errors": [],
                "manifest": [],
            }
    run_id = f"ledger_migrate_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{uuid4().hex[:8]}"
    await indexer.conn.execute(
        """CREATE TABLE IF NOT EXISTS ledger_migration_runs (
            run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, dry_run INTEGER NOT NULL,
            status TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0, migrated INTEGER NOT NULL DEFAULT 0,
            previewed INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]',
            started_at TEXT NOT NULL, completed_at TEXT,
            source_high_water INTEGER NOT NULL DEFAULT 0, last_rowid INTEGER NOT NULL DEFAULT 0,
            last_card_id TEXT NOT NULL DEFAULT '', source_snapshot_hash TEXT NOT NULL DEFAULT '',
            manifest TEXT NOT NULL DEFAULT '{}', checkpoint_at TEXT
        )"""
    )
    existing_columns_cursor = await indexer.conn.execute("PRAGMA table_info(ledger_migration_runs)")
    existing_columns = {str(row[1]) for row in await existing_columns_cursor.fetchall()}
    for name, definition in (
        ("source_high_water", "INTEGER NOT NULL DEFAULT 0"), ("last_rowid", "INTEGER NOT NULL DEFAULT 0"),
        ("last_card_id", "TEXT NOT NULL DEFAULT ''"), ("source_snapshot_hash", "TEXT NOT NULL DEFAULT ''"),
        ("manifest", "TEXT NOT NULL DEFAULT '{}'"),
        ("checkpoint_at", "TEXT"),
        ("previewed", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in existing_columns:
            await indexer.conn.execute(f"ALTER TABLE ledger_migration_runs ADD COLUMN {name} {definition}")
    high_cursor = await indexer.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM cards")
    high_water = int((await high_cursor.fetchone())[0])
    cards_info = await indexer.conn.execute("PRAGMA table_info(cards)")
    card_columns = {str(row[1]) for row in await cards_info.fetchall()}
    content_column = "content" if "content" in card_columns else "summary"
    source_anchor_column = "source_anchor" if "source_anchor" in card_columns else "NULL AS source_anchor"
    source_document_column = "source_document_id" if "source_document_id" in card_columns else "'' AS source_document_id"
    source_cursor = await indexer.conn.execute(
        f"SELECT rowid, card_id, {content_column} AS content, source_book, {source_anchor_column}, {source_document_column} FROM cards WHERE rowid <= ? ORDER BY rowid",
        (high_water,),
    )
    source_rows = await source_cursor.fetchall()
    snapshot_hash = hashlib.sha256(
        json.dumps([list(row) for row in source_rows], ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    started_at = datetime.now(timezone.utc).isoformat()
    await indexer.conn.execute(
        "INSERT INTO ledger_migration_runs(run_id, kind, dry_run, status, started_at, source_high_water, source_snapshot_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, "legacy_cards", int(dry_run), "PREVIEW" if dry_run else "RUNNING", started_at, high_water, snapshot_hash),
    )
    await indexer.conn.commit()
    rows = await indexer.conn.execute("SELECT rowid AS _migration_rowid, * FROM cards WHERE rowid <= ? ORDER BY rowid", (high_water,))
    rows = await rows.fetchall()
    migrated = 0
    previewed = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    manifest: list[dict[str, Any]] = []
    for row in rows:
        data = indexer._row_to_dict(row)
        if not data.get("source_book_id") and not data.get("source_document_id"):
            skipped += 1
            continue
        try:
            await indexer.conn.execute("SAVEPOINT ledger_card_migration")
            if not dry_run:
                await indexer._project_card_to_ledger(data)
                await indexer.conn.execute("RELEASE SAVEPOINT ledger_card_migration")
                migrated += 1
                manifest.append({"card_id": str(data.get("card_id", "")), "rowid": int(row[0]), "operation": "project"})
                await indexer.conn.execute(
                    "UPDATE ledger_migration_runs SET migrated=?, last_rowid=?, last_card_id=?, checkpoint_at=? WHERE run_id=?",
                    (migrated, int(row[0]), str(data.get("card_id", "")), datetime.now(timezone.utc).isoformat(), run_id),
                )
                await indexer.conn.commit()
            else:
                # 预览模式不写 ledger，只统计将处理的行数并记录 manifest，migrated 保持为 0。
                await indexer.conn.execute("ROLLBACK TO SAVEPOINT ledger_card_migration")
                await indexer.conn.execute("RELEASE SAVEPOINT ledger_card_migration")
                previewed += 1
                manifest.append(
                    {
                        "card_id": str(data.get("card_id", "")),
                        "rowid": int(row[0]),
                        "operation": "preview",
                        "applied": False,
                    }
                )
        except Exception as exc:
            await indexer.conn.execute("ROLLBACK TO SAVEPOINT ledger_card_migration")
            await indexer.conn.execute("RELEASE SAVEPOINT ledger_card_migration")
            errors.append({"card_id": str(data.get("card_id", "")), "error": str(exc)})
    completed_at = datetime.now(timezone.utc).isoformat()
    status = "PREVIEW" if dry_run else ("FAILED" if errors else "COMPLETED")
    await indexer.conn.execute(
        """UPDATE ledger_migration_runs SET status=?, total=?, migrated=?, previewed=?, skipped=?, errors=?, manifest=?, completed_at=? WHERE run_id=?""",
        (status, len(rows), migrated, previewed, skipped, json.dumps(errors, ensure_ascii=False), json.dumps(manifest, ensure_ascii=False), completed_at, run_id),
    )
    await indexer.conn.commit()
    return {
        "run_id": run_id,
        "dry_run": dry_run,
        "status": status,
        "total": len(rows),
        "migrated": migrated,
        "previewed": previewed,
        "skipped": skipped,
        "errors": errors,
        "manifest": manifest,
        "completed_at": completed_at,
    }
