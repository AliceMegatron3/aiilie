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
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_book ON cards(source_book)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_type ON cards(card_type, subtype)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_scores ON cards(utility_score, entropy_score)")
            await self._conn.commit()
        except Exception:
            await self._conn.close()
            self._conn = None
            raise
        logger.info("书库索引初始化完成: %s", self.db_path)

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
                    original_fragment, payload, detail_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_id) DO UPDATE SET
                    source_book=excluded.source_book, source_chapter=excluded.source_chapter,
                    card_type=excluded.card_type, category=excluded.category,
                    subtype=excluded.subtype, summary=excluded.summary, content=excluded.content,
                    tags=excluded.tags, create_time=excluded.create_time,
                    entropy_score=excluded.entropy_score, utility_score=excluded.utility_score,
                    original_fragment=excluded.original_fragment, payload=excluded.payload,
                    detail_path=excluded.detail_path""",
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
        return item

    async def search_cards(self, card_type: str | None = None, category: str | None = None,
                           subtype: str | None = None, keyword: str | None = None,
                           source_book: str | None = None, min_utility: float | None = None,
                           min_entropy: float | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        conditions: list[str] = []
        args: list[Any] = []
        for column, value in (("card_type", card_type), ("category", category), ("source_book", source_book)):
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
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        args.extend((max(1, min(int(limit), 500)), max(0, int(offset))))
        cursor = await self.conn.execute(f"SELECT * FROM cards{where} ORDER BY rowid DESC LIMIT ? OFFSET ?", args)
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def count_search_cards(self, **filters: Any) -> int:
        conditions: list[str] = []
        args: list[Any] = []
        for column, value in (("card_type", filters.get("card_type")), ("category", filters.get("category")), ("source_book", filters.get("source_book"))):
            if value is not None:
                conditions.append(f"{column} = ?")
                args.append(value)
        if filters.get("subtype") is not None:
            conditions.append("subtype LIKE ?")
            args.append(f"%{filters['subtype']}%")
        if filters.get("keyword"):
            conditions.append("(content LIKE ? OR summary LIKE ? OR tags LIKE ?)")
            pattern = f"%{filters['keyword']}%"
            args.extend((pattern, pattern, pattern))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self.conn.execute(f"SELECT COUNT(*) FROM cards{where}", args)
        row = await cursor.fetchone()
        return int(row[0] if row else 0)

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

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


__all__ = ["CardIndexer"]