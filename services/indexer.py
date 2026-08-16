"""书库卡片索引服务。

索引库只保存可检索的热数据，完整卡片同时以 JSON 形式保存到冷存储目录。
所有 SQL 使用参数绑定，所有落盘标识符都经过 ``safe_join`` 校验。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

from core.path_resolver import get_ai_index_dir, safe_join
from models.cards import BaseCard

logger = logging.getLogger(__name__)


class CardIndexer:
    """异步 SQLite 热索引 + JSON 冷存储。"""

    def __init__(self, index_dir: Path | None = None) -> None:
        self.index_dir = Path(index_dir or get_ai_index_dir())
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.index_dir / "library_index.db"
        self.cold_dir = self.index_dir / "cards"
        self.cold_dir.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("卡片索引尚未初始化")
        return self._conn

    async def initialize(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        try:
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.execute("PRAGMA busy_timeout=10000")
            await self._conn.execute(
                """CREATE TABLE IF NOT EXISTS cards (
                    card_id TEXT PRIMARY KEY, source_book TEXT NOT NULL,
                    source_chapter TEXT NOT NULL DEFAULT '', card_type TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'misc', subtype TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL, content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]', create_time TEXT NOT NULL,
                    entropy_score REAL NOT NULL DEFAULT 0.5, utility_score REAL NOT NULL DEFAULT 0.5,
                    original_fragment TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}',
                    detail_path TEXT NOT NULL
                )"""
            )
            await self._migrate_cards_schema()
            await self._conn.execute(
                """CREATE TABLE IF NOT EXISTS card_relations (
                    relation_id TEXT PRIMARY KEY,
                    source_card_id TEXT NOT NULL,
                    target_card_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL DEFAULT 'related_to',
                    weight REAL NOT NULL DEFAULT 1.0,
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active'
                )"""
            )
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_book ON cards(source_book)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_type ON cards(card_type, subtype)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_scores ON cards(utility_score, entropy_score)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_library_scope ON cards(library_id, scope_level, status)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_constraints ON cards(domain, evidence_level, rule_strength)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON card_relations(source_card_id, relation_type)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_target ON card_relations(target_card_id, relation_type)")
            await self._conn.commit()
        except Exception:
            await self._conn.close()
            self._conn = None
            raise
        logger.info("书库索引初始化完成: %s", self.db_path)

    async def _migrate_cards_schema(self) -> None:
        """Additive migration for indexes created before scoped library cards."""
        cursor = await self.conn.execute("PRAGMA table_info(cards)")
        existing = {str(row[1]) for row in await cursor.fetchall()}
        additions = {
            "library_id": "TEXT NOT NULL DEFAULT ''",
            "knowledge_type": "TEXT NOT NULL DEFAULT 'FACT'",
            "book_type_ids": "TEXT NOT NULL DEFAULT '[]'",
            "domain": "TEXT NOT NULL DEFAULT ''",
            "scope_level": "TEXT NOT NULL DEFAULT 'book'",
            "status": "TEXT NOT NULL DEFAULT 'draft'",
            "evidence_level": "TEXT NOT NULL DEFAULT 'unknown'",
            "rule_strength": "TEXT NOT NULL DEFAULT 'none'",
            "source_document_id": "TEXT NOT NULL DEFAULT ''",
            "source_anchor": "TEXT NOT NULL DEFAULT '{}'",
            "valid_time_start": "TEXT",
            "valid_time_end": "TEXT",
            "valid_places": "TEXT NOT NULL DEFAULT '[]'",
            "related_card_ids": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, definition in additions.items():
            if name not in existing:
                await self.conn.execute(f"ALTER TABLE cards ADD COLUMN {name} {definition}")

    @staticmethod
    def _card_dict(card: BaseCard) -> dict[str, Any]:
        return card.model_dump(mode="json")

    def _detail_path(self, card_id: str) -> Path:
        return safe_join(self.cold_dir, f"{card_id}.json")

    async def save_card(self, card: BaseCard) -> str:
        data = self._card_dict(card)
        card_id = str(data["card_id"])
        detail_path = self._detail_path(card_id)
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        async with self._lock:
            await asyncio.to_thread(detail_path.write_text, raw, encoding="utf-8")
            await self.conn.execute(
                """INSERT INTO cards (
                    card_id, source_book, source_chapter, card_type, category, subtype,
                    summary, content, tags, create_time, entropy_score, utility_score,
                    original_fragment, payload, detail_path, library_id, knowledge_type,
                    book_type_ids, domain, scope_level, status, evidence_level,
                    rule_strength, source_document_id, source_anchor, valid_time_start,
                    valid_time_end, valid_places, related_card_ids
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(card_id) DO UPDATE SET
                    source_book=excluded.source_book, source_chapter=excluded.source_chapter,
                    card_type=excluded.card_type, category=excluded.category,
                    subtype=excluded.subtype, summary=excluded.summary, content=excluded.content,
                    tags=excluded.tags, create_time=excluded.create_time,
                    entropy_score=excluded.entropy_score, utility_score=excluded.utility_score,
                    original_fragment=excluded.original_fragment, payload=excluded.payload,
                    detail_path=excluded.detail_path, library_id=excluded.library_id,
                    knowledge_type=excluded.knowledge_type, book_type_ids=excluded.book_type_ids,
                    domain=excluded.domain, scope_level=excluded.scope_level,
                    status=excluded.status, evidence_level=excluded.evidence_level,
                    rule_strength=excluded.rule_strength,
                    source_document_id=excluded.source_document_id,
                    source_anchor=excluded.source_anchor,
                    valid_time_start=excluded.valid_time_start,
                    valid_time_end=excluded.valid_time_end,
                    valid_places=excluded.valid_places,
                    related_card_ids=excluded.related_card_ids""",
                (
                    card_id, data.get("source_book_id", ""), data.get("source_chapter", ""),
                    data.get("card_type", "info"), data.get("category", "misc"),
                    data.get("card_sub_type", data.get("metric_type", "")),
                    data.get("content", ""), data.get("content", ""),
                    json.dumps(data.get("tags", []), ensure_ascii=False), data.get("create_time", ""),
                    float(data.get("entropy_score", 0.5)), float(data.get("utility_score", 0.5)),
                    data.get("original_fragment", ""),
                    json.dumps(data.get("payload", data.get("value", {})), ensure_ascii=False, default=str),
                    str(detail_path),
                    data.get("library_id", ""), data.get("knowledge_type", "FACT"),
                    json.dumps(data.get("book_type_ids", []), ensure_ascii=False), data.get("domain", ""),
                    data.get("scope_level", "book"), data.get("status", "draft"),
                    data.get("evidence_level", "unknown"), data.get("rule_strength", "none"),
                    data.get("source_document_id", ""),
                    json.dumps(data.get("source_anchor") or {}, ensure_ascii=False, default=str),
                    data.get("valid_time_start"), data.get("valid_time_end"),
                    json.dumps(data.get("valid_places", []), ensure_ascii=False),
                    json.dumps(data.get("related_card_ids", []), ensure_ascii=False),
                ),
            )
            await self.conn.execute("DELETE FROM card_relations WHERE source_card_id = ?", (card_id,))
            for relation in data.get("relations", []):
                if not relation.get("target_card_id"):
                    continue
                await self.conn.execute(
                    """INSERT INTO card_relations
                    (relation_id, source_card_id, target_card_id, relation_type, weight, note, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        relation.get("relation_id") or f"{card_id}:{relation['target_card_id']}",
                        card_id, relation["target_card_id"], relation.get("relation_type", "related_to"),
                        float(relation.get("weight", 1.0)), relation.get("note", ""),
                        relation.get("status", "active"),
                    ),
                )
            await self.conn.commit()
        return card_id

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["tags"] = json.loads(item.get("tags") or "[]")
        except (TypeError, ValueError):
            item["tags"] = []
        item["source_book_id"] = item.get("source_book", "")
        item["card_sub_type"] = item.get("subtype", "")
        for field, default in (("book_type_ids", []), ("valid_places", []), ("related_card_ids", []), ("source_anchor", {})):
            try:
                item[field] = json.loads(item.get(field) or json.dumps(default, ensure_ascii=False))
            except (TypeError, ValueError):
                item[field] = default
        return item

    async def search_cards(self, card_type: str | None = None, category: str | None = None,
                           subtype: str | None = None, keyword: str | None = None,
                           source_book: str | None = None, min_utility: float | None = None,
                           min_entropy: float | None = None, library_id: str | None = None,
                           knowledge_type: str | None = None, domain: str | None = None,
                           scope_level: str | None = None, status: str | None = None,
                           evidence_level: str | None = None, rule_strength: str | None = None,
                           book_type_id: str | None = None, place: str | None = None,
                           time_at: str | None = None, relation_to: str | None = None,
                           relation_type: str | None = None, limit: int = 50,
                           offset: int = 0) -> list[dict[str, Any]]:
        conditions: list[str] = []
        args: list[Any] = []
        for column, value in (
            ("card_type", card_type), ("category", category), ("source_book", source_book),
            ("library_id", library_id), ("knowledge_type", knowledge_type), ("domain", domain),
            ("scope_level", scope_level), ("status", status), ("evidence_level", evidence_level),
            ("rule_strength", rule_strength),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                args.append(value)
        if subtype is not None:
            conditions.append("subtype LIKE ?")
            args.append(f"%{subtype}%")
        if keyword:
            conditions.append("(content LIKE ? OR summary LIKE ? OR tags LIKE ?)")
            pattern = f"%{keyword}%"
            args.extend((pattern, pattern, pattern))
        if min_utility is not None:
            conditions.append("utility_score >= ?")
            args.append(min_utility)
        if min_entropy is not None:
            conditions.append("entropy_score >= ?")
            args.append(min_entropy)
        if book_type_id:
            conditions.append("book_type_ids LIKE ?")
            args.append(f'%"{book_type_id}"%')
        if place:
            conditions.append("valid_places LIKE ?")
            args.append(f"%{place}%")
        if time_at:
            conditions.append("(valid_time_start IS NULL OR valid_time_start <= ?)")
            conditions.append("(valid_time_end IS NULL OR valid_time_end >= ?)")
            args.extend((time_at, time_at))
        if relation_to or relation_type:
            relation_conditions = ["card_relations.source_card_id = cards.card_id"]
            if relation_to:
                relation_conditions.append("card_relations.target_card_id = ?")
                args.append(relation_to)
            if relation_type:
                relation_conditions.append("card_relations.relation_type = ?")
                args.append(relation_type)
            conditions.append(f"EXISTS (SELECT 1 FROM card_relations WHERE {' AND '.join(relation_conditions)})")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        args.extend((max(1, min(int(limit), 500)), max(0, int(offset))))
        cursor = await self.conn.execute(f"SELECT * FROM cards{where} ORDER BY rowid DESC LIMIT ? OFFSET ?", args)
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def count_search_cards(self, **filters: Any) -> int:
        # Preserve the public count API while sharing filtering behavior with
        # search_cards.  The capped result is sufficient for UI pagination.
        return len(await self.search_cards(limit=500, **filters))

    async def get_card_detail(self, card_id: str) -> dict[str, Any] | None:
        detail_path = self._detail_path(card_id)
        if detail_path.exists():
            try:
                return json.loads(await asyncio.to_thread(detail_path.read_text, encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("卡片冷数据损坏，将回退热索引: %s", card_id)
        cursor = await self.conn.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,))
        row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

    async def get_card_summaries(self, source_book: str | None = None, limit: int = 50,
                                 subtype_like: str | None = None) -> list[str]:
        rows = await self.search_cards(source_book=source_book, subtype=subtype_like, limit=limit)
        return [str(row.get("summary") or row.get("content") or "") for row in rows]

    async def delete_card(self, card_id: str) -> bool:
        detail_path = self._detail_path(card_id)
        cursor = await self.conn.execute("DELETE FROM cards WHERE card_id = ?", (card_id,))
        await self.conn.commit()
        if cursor.rowcount:
            try:
                await asyncio.to_thread(detail_path.unlink, missing_ok=True)
            except OSError:
                logger.warning("卡片索引已删除，但冷数据清理失败: %s", card_id)
            return True
        return False

    async def update_card_metadata(self, card_id: str, **updates: Any) -> bool:
        """Update audited metadata without requiring a concrete card subclass."""
        allowed = {
            "library_id", "knowledge_type", "book_type_ids", "domain", "scope_level",
            "status", "evidence_level", "rule_strength", "source_document_id",
            "source_anchor", "valid_time_start", "valid_time_end", "valid_places",
            "related_card_ids", "tags", "utility_score", "entropy_score",
        }
        changes = {key: value for key, value in updates.items() if key in allowed}
        if not changes:
            return False
        detail = await self.get_card_detail(card_id)
        if detail is None:
            return False
        for field in ("book_type_ids", "source_anchor", "valid_places", "related_card_ids", "tags"):
            if field in changes:
                detail[field] = changes[field]
        serialized = {"book_type_ids", "source_anchor", "valid_places", "related_card_ids", "tags"}
        assignments: list[str] = []
        args: list[Any] = []
        for key, value in changes.items():
            assignments.append(f"{key} = ?")
            args.append(json.dumps(value, ensure_ascii=False, default=str) if key in serialized else value)
        args.append(card_id)
        async with self._lock:
            await self.conn.execute(f"UPDATE cards SET {', '.join(assignments)} WHERE card_id = ?", args)
            await self.conn.commit()
            await asyncio.to_thread(
                self._detail_path(card_id).write_text,
                json.dumps(detail, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        return True

    async def upsert_relation(
        self, source_card_id: str, target_card_id: str, relation_type: str = "related_to",
        weight: float = 1.0, note: str = "",
    ) -> str:
        relation_id = f"{source_card_id}:{relation_type}:{target_card_id}"
        await self.conn.execute(
            """INSERT INTO card_relations
            (relation_id, source_card_id, target_card_id, relation_type, weight, note, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            ON CONFLICT(relation_id) DO UPDATE SET
                weight=excluded.weight, note=excluded.note, status='active'""",
            (relation_id, source_card_id, target_card_id, relation_type, float(weight), note),
        )
        await self.conn.commit()
        return relation_id

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


__all__ = ["CardIndexer"]