"""书库卡片索引服务。

索引库只保存可检索的热数据，完整卡片同时以 JSON 形式保存到冷存储目录。
所有 SQL 使用参数绑定，所有落盘标识符都经过 ``safe_join`` 校验。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

from core.path_resolver import get_ai_index_dir, get_library_index_dir, safe_join
from models.cards import BaseCard
from core.config_manager import config_manager

logger = logging.getLogger(__name__)


class CardIndexer:
    """异步 SQLite 热索引 + JSON 冷存储。"""

    def __init__(self, index_dir: Path | None = None) -> None:
        self.index_dir = Path(index_dir or get_library_index_dir())
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.index_dir / "library_index.db"
        self.cold_dir = self.index_dir / "cards"
        self.cold_dir.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._fts_available = False

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
            await self._initialize_ledger_tables()
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_book ON cards(source_book)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_type ON cards(card_type, subtype)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_scores ON cards(utility_score, entropy_score)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_library_scope ON cards(library_id, scope_level, status)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_constraints ON cards(domain, evidence_level, rule_strength)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON card_relations(source_card_id, relation_type)")
            await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_target ON card_relations(target_card_id, relation_type)")
            await self._initialize_fts()
            await self._conn.commit()
        except Exception:
            await self._conn.close()
            self._conn = None
            raise
        for warning in await self._detect_diverged_workspace_index():
            logger.warning("%s", warning)
        logger.info("书库索引初始化完成: %s", self.db_path)

    async def _detect_diverged_workspace_index(self) -> list[str]:
        """检测是否仍存在统一前的 workspace 暂存索引库（双库并存告警）。

        当权威路径（self.index_dir）与 workspace 下的
        get_ai_index_dir()/library_index.db 不同、且该 workspace 库存在并
        含有卡片数据时，说明历史版本曾把 workspace 库当作活动索引库使用。
        返回告警信息列表（空列表 = 无分歧）；只读探测，不删除任何文件。
        """
        workspace_index_db = get_ai_index_dir() / "library_index.db"
        if workspace_index_db == self.db_path:
            return []
        if not workspace_index_db.exists():
            return []
        try:
            count = await self._count_cards_in(workspace_index_db)
        except Exception as exc:  # pragma: no cover - 只读探测失败不阻断初始化
            logger.warning("双库检测无法读取 workspace 索引库: %s (%s)", workspace_index_db, exc)
            return []
        if count <= 0:
            return []
        return [
            f"检测到双库并存：workspace 暂存索引库 {workspace_index_db} 仍含 {count} 张卡片，"
            f"现已统一到权威索引库 {self.db_path}；workspace 库将不再作为活动索引库"
            "（未删除任何文件）。"
        ]

    async def _count_cards_in(self, db_path: Path) -> int:
        """以只读方式统计指定索引库的卡片数（不写入、不创建任何新文件）。"""
        uri = db_path.as_uri() + "?mode=ro"
        conn = await aiosqlite.connect(uri, uri=True)
        try:
            cursor = await conn.execute("SELECT COUNT(*) FROM cards")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        finally:
            await conn.close()

    async def _initialize_ledger_tables(self) -> None:
        """创建 Ledger 最小表；卡片表继续作为兼容投影。"""
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ledger_documents (
                document_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                source_uri TEXT NOT NULL DEFAULT '', media_type TEXT NOT NULL DEFAULT 'text/plain',
                content_hash TEXT NOT NULL DEFAULT '', parser_id TEXT NOT NULL DEFAULT 'legacy',
                parser_version TEXT NOT NULL DEFAULT '', license_status TEXT NOT NULL DEFAULT 'unknown',
                status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_passages (
                passage_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                heading TEXT NOT NULL DEFAULT '', quote TEXT NOT NULL DEFAULT '', text_hash TEXT NOT NULL DEFAULT '',
                char_start INTEGER, char_end INTEGER, page_start INTEGER, page_end INTEGER,
                status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_evidence (
                evidence_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, passage_id TEXT NOT NULL,
                quote TEXT NOT NULL DEFAULT '', anchor TEXT NOT NULL DEFAULT '{}', extraction_run_id TEXT NOT NULL DEFAULT '',
                evidence_level TEXT NOT NULL DEFAULT 'unknown', confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'verified', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_claims (
                claim_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, evidence_ids TEXT NOT NULL DEFAULT '[]',
                claim_type TEXT NOT NULL DEFAULT 'FACT', content TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
                scope_level TEXT NOT NULL DEFAULT 'book', confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_metrics (
                metric_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, name TEXT NOT NULL,
                value TEXT NOT NULL DEFAULT 'null', unit TEXT NOT NULL DEFAULT '', method TEXT NOT NULL DEFAULT '',
                sample_size INTEGER NOT NULL DEFAULT 0, confidence_low REAL, confidence_high REAL,
                semantic_level TEXT NOT NULL DEFAULT 'descriptive', source_claim_ids TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_relations (
                relation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL DEFAULT 'related_to', weight REAL NOT NULL DEFAULT 1.0,
                note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_run_audits (
                run_id TEXT PRIMARY KEY, stage TEXT NOT NULL, artifact_id TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '', prompt_version TEXT NOT NULL DEFAULT '', tokens INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'completed',
                error TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_law_branches (
                branch_id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_branch_id TEXT,
                status TEXT NOT NULL DEFAULT 'active', current_revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_law_revisions (
                branch_id TEXT NOT NULL, revision INTEGER NOT NULL, operation TEXT NOT NULL DEFAULT 'snapshot',
                snapshot TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                PRIMARY KEY(branch_id, revision)
            );
            CREATE TABLE IF NOT EXISTS ledger_derived_artifacts (
                artifact_id TEXT PRIMARY KEY, source_scope TEXT NOT NULL, artifact_type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'draft',
                source_document_id TEXT NOT NULL DEFAULT '', source_run_id TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_derived_scope ON ledger_derived_artifacts(source_scope, artifact_type, status);
            CREATE TABLE IF NOT EXISTS ledger_outbox (
                event_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, operation TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL, applied_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_outbox_artifact_operation ON ledger_outbox(artifact_id, operation);
            CREATE TABLE IF NOT EXISTS ledger_tombstones (
                artifact_id TEXT PRIMARY KEY, artifact_type TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, deleted_by TEXT NOT NULL DEFAULT 'system'
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_passages_document ON ledger_passages(document_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_ledger_evidence_passage ON ledger_evidence(passage_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_claims_document ON ledger_claims(document_id, status);
            CREATE INDEX IF NOT EXISTS idx_ledger_relations_source ON ledger_relations(source_id, relation_type);
            """
        )

    async def _initialize_fts(self) -> None:
        """初始化卡片全文投影；失败时保留 LIKE 检索兼容。"""
        try:
            await self.conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
                    card_id UNINDEXED, source_book UNINDEXED, subtype UNINDEXED,
                    summary, content, tags, original_fragment,
                    tokenize='unicode61'
                )"""
            )
            self._fts_available = True
            cursor = await self.conn.execute("SELECT COUNT(*) FROM cards_fts")
            indexed = int((await cursor.fetchone())[0])
            cursor = await self.conn.execute("SELECT COUNT(*) FROM cards")
            cards_count = int((await cursor.fetchone())[0])
            if indexed != cards_count:
                await self.conn.execute("DELETE FROM cards_fts")
                await self.conn.execute(
                    """INSERT INTO cards_fts(card_id, source_book, subtype, summary, content, tags, original_fragment)
                       SELECT card_id, source_book, subtype, summary, content, tags, original_fragment FROM cards"""
                )
        except aiosqlite.OperationalError as exc:
            logger.warning("SQLite FTS5 不可用，继续使用兼容 LIKE 检索: %s", exc)

    async def _index_card_fts(self, data: dict[str, Any]) -> None:
        try:
            await self.conn.execute("DELETE FROM cards_fts WHERE card_id = ?", (data["card_id"],))
            await self.conn.execute(
                """INSERT INTO cards_fts(card_id, source_book, subtype, summary, content, tags, original_fragment)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["card_id"], data.get("source_book_id", ""),
                    data.get("card_sub_type", data.get("metric_type", data.get("extension_module", ""))),
                    data.get("content", ""), data.get("content", ""),
                    json.dumps(data.get("tags", []), ensure_ascii=False), data.get("original_fragment", ""),
                ),
            )
        except aiosqlite.OperationalError:
            pass

    async def _project_card_to_ledger(self, data: dict[str, Any]) -> None:
        """将旧卡片投影为 Ledger 文档、段落、证据和命题。"""
        from datetime import datetime, timezone

        card_id = str(data["card_id"])
        document_id = str(data.get("source_document_id") or data.get("source_book_id") or "")
        if not document_id:
            return
        content = str(data.get("content") or "")
        source_anchor = data.get("source_anchor") or {}
        if isinstance(source_anchor, str):
            try:
                source_anchor = json.loads(source_anchor)
            except (TypeError, ValueError):
                source_anchor = {}
        now = datetime.now(timezone.utc).isoformat()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        passage_id = f"passage_{card_id}"
        evidence_id = f"evidence_{card_id}"
        # 每张卡是独立 ledger 工件：fingerprint 必须包含 card_id，否则内容相同的
        # legacy 派生卡（如未回填 content 时 content='' 的模板化摘要）会互相碰撞，
        # 触发 ledger_claims.fingerprint UNIQUE 约束冲突。非权威 draft 语义下可接受。
        fingerprint = hashlib.sha256(
            "|".join((document_id, card_id, str(data.get("source_chapter", "")), content)).encode("utf-8")
        ).hexdigest()
        await self.conn.execute(
            """INSERT INTO ledger_documents(document_id, title, source_uri, content_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id) DO UPDATE SET
                 title=excluded.title, source_uri=excluded.source_uri,
                 content_hash=excluded.content_hash, updated_at=excluded.updated_at""",
            (document_id, document_id, str(source_anchor.get("source_path", "")), content_hash, now, now),
        )
        await self.conn.execute(
            """INSERT INTO ledger_passages
               (passage_id, document_id, sequence, heading, quote, text_hash, char_start, char_end, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(passage_id) DO UPDATE SET
                 quote=excluded.quote, text_hash=excluded.text_hash,
                 char_start=excluded.char_start, char_end=excluded.char_end""",
            (
                passage_id, document_id, int(source_anchor.get("paragraph", 0) or 0),
                str(data.get("source_chapter", "")), content[:2000], content_hash,
                source_anchor.get("char_start"), source_anchor.get("char_end"), now,
            ),
        )
        await self.conn.execute(
            """INSERT INTO ledger_evidence
               (evidence_id, document_id, passage_id, quote, anchor, evidence_level, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(evidence_id) DO UPDATE SET
                 quote=excluded.quote, anchor=excluded.anchor,
                 evidence_level=excluded.evidence_level, confidence=excluded.confidence""",
            (
                evidence_id, document_id, passage_id,
                str(data.get("original_fragment", "")) or content[:200],
                json.dumps(source_anchor, ensure_ascii=False, default=str),
                data.get("evidence_level", "unknown"),
                float(data.get("utility_score", 0.0) or 0.0), now,
            ),
        )
        await self.conn.execute(
            """INSERT INTO ledger_claims
               (claim_id, document_id, evidence_ids, claim_type, content, fingerprint,
                scope_level, confidence, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(claim_id) DO UPDATE SET
                 evidence_ids=excluded.evidence_ids, claim_type=excluded.claim_type,
                 content=excluded.content, fingerprint=excluded.fingerprint,
                 scope_level=excluded.scope_level, confidence=excluded.confidence,
                 status=excluded.status, updated_at=excluded.updated_at""",
            (
                card_id, document_id, json.dumps([evidence_id]), data.get("knowledge_type", "FACT"),
                content, fingerprint, data.get("scope_level", "book"),
                float(data.get("utility_score", 0.0) or 0.0), data.get("status", "draft"), now, now,
            ),
        )
        if data.get("card_type") == "data":
            metric_id = f"metric_{card_id}"
            await self.conn.execute(
                """INSERT INTO ledger_metrics
                   (metric_id, document_id, name, value, method, sample_size, semantic_level,
                    source_claim_ids, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(metric_id) DO UPDATE SET
                     name=excluded.name, value=excluded.value, method=excluded.method,
                     source_claim_ids=excluded.source_claim_ids, status=excluded.status""",
                (
                    metric_id, document_id, data.get("metric_type", data.get("subtype", "metric")),
                    json.dumps(data.get("value", data.get("payload", {})), ensure_ascii=False, default=str),
                    "legacy_card_projection", 1, "descriptive", json.dumps([card_id]),
                    data.get("status", "draft"), now,
                ),
            )
        for relation in data.get("relations", []):
            target = relation.get("target_card_id")
            if not target:
                continue
            await self.conn.execute(
                """INSERT INTO ledger_relations
                   (relation_id, source_id, target_id, relation_type, weight, note, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(relation_id) DO UPDATE SET
                     weight=excluded.weight, note=excluded.note, status=excluded.status""",
                (
                    relation.get("relation_id") or f"{card_id}:{target}", card_id, target,
                    relation.get("relation_type", "related_to"), float(relation.get("weight", 1.0)),
                    relation.get("note", ""), relation.get("status", "active"), now,
                ),
            )
        await self.conn.execute(
            """INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at)
               VALUES (?, ?, 'CARD_PROJECTED', ?, 'PENDING', ?)
               ON CONFLICT(event_id) DO UPDATE SET payload=excluded.payload, status='PENDING', created_at=excluded.created_at, applied_at=NULL""",
            (
                f"card_projected:{card_id}", card_id,
                json.dumps({"document_id": document_id, "claim_id": card_id}, ensure_ascii=False),
                now,
            ),
        )

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
        """保存单张卡片；批量调用方应使用 :meth:`save_cards`。"""
        card_ids = await self.save_cards([card])
        return card_ids[0]

    async def save_cards(self, cards: list[BaseCard]) -> list[str]:
        """在一个锁和一个 SQLite 事务中幂等保存多张卡片。"""
        if not cards:
            return []
        authoritative = config_manager.get_bool("ledger.authoritative", False)
        if authoritative:
            # 来源契约门禁：权威模式下拒绝无来源锚点的卡片（与旧投影开关无关）。
            missing = [str(card.card_id) for card in cards if not card.source_document_id or not card.source_anchor]
            if missing:
                raise ValueError(f"Ledger 权威模式拒绝无来源锚点卡片: {missing[:5]}")
        # 权威 + 关闭 legacy 投影 → 只写 Ledger（claims+outbox+冷归档），
        # 不再写旧 cards 表 / card_relations / FTS；cards 表冻结为只读投影。
        ledger_only = authoritative and not config_manager.get_bool("ledger.allow_legacy_card_projection", True)
        card_ids: list[str] = []
        async with self._lock:
            try:
                for card in cards:
                    data = self._card_dict(card)
                    card_id = str(data["card_id"])
                    detail_path = self._detail_path(card_id)
                    raw = json.dumps(data, ensure_ascii=False, indent=2)
                    await asyncio.to_thread(detail_path.write_text, raw, encoding="utf-8")
                    if not ledger_only:
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
                                data.get("card_sub_type", data.get("metric_type", data.get("extension_module", ""))),
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
                        await self._index_card_fts(data)
                    await self._project_card_to_ledger(data)
                    # 阶段C：ledger-only 模式下 cards 表未写入投影，不得再写入 legacy
                    # CARD_PROJECTED 确认事件，否则 drain_compat 校验卡表缺失 → DEAD_LETTER。
                    if not ledger_only:
                        await self.conn.execute(
                            """INSERT OR IGNORE INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at)
                               VALUES (?, ?, 'CARD_PROJECTED', ?, 'APPLIED', ?)""",
                            (
                                f"card_projected:{card_id}", card_id,
                                json.dumps({"document_id": data.get("source_document_id") or data.get("source_book_id", "")}, ensure_ascii=False),
                                data.get("create_time", ""),
                            ),
                        )
                    if not ledger_only:
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
                    card_ids.append(card_id)
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise
        return card_ids

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
            pattern = f"%{keyword}%"
            if self._fts_available:
                # FTS5 负责英文/分词检索；LIKE 作为中文与旧 tokenizer 的兼容补偿。
                conditions.append(
                    "(card_id IN (SELECT card_id FROM cards_fts WHERE cards_fts MATCH ?) "
                    "OR content LIKE ? OR summary LIKE ? OR tags LIKE ?)"
                )
                args.extend(('"' + keyword.replace('"', '""') + '"', pattern, pattern, pattern))
            else:
                conditions.append("(content LIKE ? OR summary LIKE ? OR tags LIKE ?)")
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
        """使用 SQL COUNT 返回准确命中数，不受分页上限影响。"""
        conditions: list[str] = []
        args: list[Any] = []
        for column, value in (
            ("card_type", filters.get("card_type")),
            ("category", filters.get("category")),
            ("source_book", filters.get("source_book")),
            ("library_id", filters.get("library_id")),
            ("knowledge_type", filters.get("knowledge_type")),
            ("domain", filters.get("domain")),
            ("scope_level", filters.get("scope_level")),
            ("status", filters.get("status")),
            ("evidence_level", filters.get("evidence_level")),
            ("rule_strength", filters.get("rule_strength")),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                args.append(value)
        if filters.get("subtype") is not None:
            conditions.append("subtype LIKE ?")
            args.append(f"%{filters['subtype']}%")
        if filters.get("keyword"):
            pattern = f"%{filters['keyword']}%"
            if self._fts_available:
                conditions.append(
                    "(card_id IN (SELECT card_id FROM cards_fts WHERE cards_fts MATCH ?) "
                    "OR content LIKE ? OR summary LIKE ? OR tags LIKE ?)"
                )
                args.extend(('"' + str(filters["keyword"]).replace('"', '""') + '"', pattern, pattern, pattern))
            else:
                conditions.append("(content LIKE ? OR summary LIKE ? OR tags LIKE ?)")
                args.extend((pattern, pattern, pattern))
        if filters.get("min_utility") is not None:
            conditions.append("utility_score >= ?")
            args.append(filters["min_utility"])
        if filters.get("min_entropy") is not None:
            conditions.append("entropy_score >= ?")
            args.append(filters["min_entropy"])
        if filters.get("book_type_id"):
            conditions.append("book_type_ids LIKE ?")
            args.append(f'%"{filters["book_type_id"]}"%')
        if filters.get("place"):
            conditions.append("valid_places LIKE ?")
            args.append(f"%{filters['place']}%")
        if filters.get("time_at"):
            conditions.append("(valid_time_start IS NULL OR valid_time_start <= ?)")
            conditions.append("(valid_time_end IS NULL OR valid_time_end >= ?)")
            args.extend((filters["time_at"], filters["time_at"]))
        if filters.get("relation_to") or filters.get("relation_type"):
            relation_conditions = ["card_relations.source_card_id = cards.card_id"]
            if filters.get("relation_to"):
                relation_conditions.append("card_relations.target_card_id = ?")
                args.append(filters["relation_to"])
            if filters.get("relation_type"):
                relation_conditions.append("card_relations.relation_type = ?")
                args.append(filters["relation_type"])
            conditions.append(f"EXISTS (SELECT 1 FROM card_relations WHERE {' AND '.join(relation_conditions)})")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self.conn.execute(f"SELECT COUNT(*) FROM cards{where}", args)
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def get_card_detail(self, card_id: str) -> dict[str, Any] | None:
        detail_path = self._detail_path(card_id)
        cold_detail: dict[str, Any] | None = None
        if detail_path.exists():
            try:
                cold_detail = json.loads(await asyncio.to_thread(detail_path.read_text, encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("卡片冷数据损坏，将回退热索引: %s", card_id)
        cursor = await self.conn.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,))
        row = await cursor.fetchone()
        if not row:
            return cold_detail
        # SQLite 热索引是筛选与审核状态的权威来源；冷 JSON 保留完整 payload。
        hot = self._row_to_dict(row)
        if cold_detail is None:
            return hot
        for field in (
            "card_id", "source_book_id", "source_chapter", "card_type", "category",
            "card_sub_type", "summary", "content", "tags", "create_time",
            "entropy_score", "utility_score", "library_id", "knowledge_type",
            "book_type_ids", "domain", "scope_level", "status", "evidence_level",
            "rule_strength", "source_document_id", "source_anchor", "valid_time_start",
            "valid_time_end", "valid_places", "related_card_ids",
        ):
            if field in hot:
                cold_detail[field] = hot[field]
        return cold_detail

    async def list_ledger_documents(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM ledger_documents ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (max(1, min(int(limit), 500)), max(0, int(offset))),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_ledger_document(self, document_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute("SELECT * FROM ledger_documents WHERE document_id = ?", (document_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        document = dict(row)
        for table, key in (("ledger_passages", "passages"), ("ledger_evidence", "evidence"), ("ledger_claims", "claims"), ("ledger_metrics", "metrics")):
            cursor = await self.conn.execute(f"SELECT * FROM {table} WHERE document_id = ? ORDER BY created_at", (document_id,))
            document[key] = [dict(item) for item in await cursor.fetchall()]
        return document

    async def list_ledger_evidence(self, document_id: str | None = None, passage_id: str | None = None,
                                   limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        conditions: list[str] = []
        args: list[Any] = []
        if document_id:
            conditions.append("document_id = ?")
            args.append(document_id)
        if passage_id:
            conditions.append("passage_id = ?")
            args.append(passage_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        args.extend((max(1, min(int(limit), 500)), max(0, int(offset))))
        cursor = await self.conn.execute(f"SELECT * FROM ledger_evidence{where} ORDER BY created_at DESC LIMIT ? OFFSET ?", args)
        return [dict(row) for row in await cursor.fetchall()]

    async def list_ledger_metrics(self, document_id: str | None = None, semantic_level: str | None = None,
                                  limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        conditions: list[str] = []
        args: list[Any] = []
        if document_id:
            conditions.append("document_id = ?")
            args.append(document_id)
        if semantic_level:
            conditions.append("semantic_level = ?")
            args.append(semantic_level)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        args.extend((max(1, min(int(limit), 500)), max(0, int(offset))))
        cursor = await self.conn.execute(f"SELECT * FROM ledger_metrics{where} ORDER BY created_at DESC LIMIT ? OFFSET ?", args)
        results = []
        for row in await cursor.fetchall():
            item = dict(row)
            try:
                item["value"] = json.loads(item.get("value") or "null")
                item["source_claim_ids"] = json.loads(item.get("source_claim_ids") or "[]")
            except (TypeError, ValueError):
                pass
            results.append(item)
        return results

    async def create_law_branch(self, branch: Any) -> dict[str, Any]:
        data = branch.model_dump(mode="json") if hasattr(branch, "model_dump") else dict(branch)
        now = data.get("created_at") or __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        await self.conn.execute(
            """INSERT INTO ledger_law_branches
               (branch_id, name, parent_branch_id, status, current_revision, created_at, updated_at)
               VALUES (?, ?, ?, ?, 0, ?, ?)""",
            (data["branch_id"], data["name"], data.get("parent_branch_id"), data.get("status", "active"), now, now),
        )
        await self.conn.execute(
            """INSERT INTO ledger_law_revisions
               (branch_id, revision, operation, snapshot, note, created_at)
               VALUES (?, 0, 'snapshot', ?, ?, ?)""",
            (data["branch_id"], json.dumps({}, ensure_ascii=False), "initial branch", now),
        )
        await self.conn.commit()
        data["created_at"] = now
        data["updated_at"] = now
        data["current_revision"] = 0
        return data

    async def list_law_branches(self) -> list[dict[str, Any]]:
        cursor = await self.conn.execute("SELECT * FROM ledger_law_branches ORDER BY updated_at DESC")
        return [dict(row) for row in await cursor.fetchall()]

    async def append_law_revision(self, branch_id: str, operation: str, snapshot: dict[str, Any], note: str = "") -> dict[str, Any]:
        cursor = await self.conn.execute("SELECT current_revision FROM ledger_law_branches WHERE branch_id = ?", (branch_id,))
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("法则分支不存在")
        revision = int(row[0]) + 1
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        await self.conn.execute(
            """INSERT INTO ledger_law_revisions(branch_id, revision, operation, snapshot, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (branch_id, revision, operation, json.dumps(snapshot, ensure_ascii=False, default=str), note, now),
        )
        await self.conn.execute(
            "UPDATE ledger_law_branches SET current_revision = ?, updated_at = ? WHERE branch_id = ?",
            (revision, now, branch_id),
        )
        await self.conn.commit()
        return {"branch_id": branch_id, "revision": revision, "operation": operation, "snapshot": snapshot, "note": note, "created_at": now}

    async def list_law_revisions(self, branch_id: str) -> list[dict[str, Any]]:
        cursor = await self.conn.execute("SELECT * FROM ledger_law_revisions WHERE branch_id = ? ORDER BY revision DESC", (branch_id,))
        results = []
        for row in await cursor.fetchall():
            item = dict(row)
            try:
                item["snapshot"] = json.loads(item.get("snapshot") or "{}")
            except (TypeError, ValueError):
                item["snapshot"] = {}
            results.append(item)
        return results

    async def save_ledger_law(self, law: Any) -> dict[str, Any]:
        data = law.model_dump(mode="json") if hasattr(law, "model_dump") else dict(law)
        now = data.get("created_at") or __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        await self.conn.execute(
            """CREATE TABLE IF NOT EXISTS ledger_laws (
                law_id TEXT NOT NULL, version INTEGER NOT NULL, layer TEXT NOT NULL,
                name TEXT NOT NULL, variables TEXT NOT NULL DEFAULT '{}', expression TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT '{}', priority INTEGER NOT NULL DEFAULT 100,
                strength TEXT NOT NULL DEFAULT 'advisory', status TEXT NOT NULL DEFAULT 'draft',
                source_evidence_ids TEXT NOT NULL DEFAULT '[]', supersedes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, PRIMARY KEY(law_id, version)
            )"""
        )
        await self.conn.execute(
            """INSERT INTO ledger_laws
               (law_id, version, layer, name, variables, expression, scope, priority, strength,
                status, source_evidence_ids, supersedes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(law_id, version) DO UPDATE SET
                 layer=excluded.layer, name=excluded.name, variables=excluded.variables,
                 expression=excluded.expression, scope=excluded.scope, priority=excluded.priority,
                 strength=excluded.strength, status=excluded.status,
                 source_evidence_ids=excluded.source_evidence_ids, supersedes=excluded.supersedes""",
            (
                data["law_id"], data.get("version", 1), data["layer"], data["name"],
                json.dumps(data.get("variables", {}), ensure_ascii=False), data.get("expression", ""),
                json.dumps(data.get("scope", {}), ensure_ascii=False), data.get("priority", 100),
                data.get("strength", "advisory"), data.get("status", "draft"),
                json.dumps(data.get("source_evidence_ids", []), ensure_ascii=False),
                json.dumps(data.get("supersedes", []), ensure_ascii=False), now,
            ),
        )
        await self.conn.commit()
        return data

    async def list_ledger_laws(self, layer: str | None = None, status: str | None = None,
                               limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        try:
            conditions: list[str] = []
            args: list[Any] = []
            if layer:
                conditions.append("layer = ?")
                args.append(layer)
            if status:
                conditions.append("status = ?")
                args.append(status)
            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            args.extend((max(1, min(int(limit), 500)), max(0, int(offset))))
            cursor = await self.conn.execute(f"SELECT * FROM ledger_laws{where} ORDER BY created_at DESC LIMIT ? OFFSET ?", args)
            results = []
            for row in await cursor.fetchall():
                item = dict(row)
                for field in ("variables", "scope", "source_evidence_ids", "supersedes"):
                    try:
                        item[field] = json.loads(item.get(field) or ("[]" if field in ("source_evidence_ids", "supersedes") else "{}"))
                    except (TypeError, ValueError):
                        pass
                results.append(item)
            return results
        except aiosqlite.OperationalError:
            return []

    async def get_card_summaries(self, source_book: str | None = None, limit: int = 50,
                                 subtype_like: str | None = None) -> list[str]:
        rows = await self.search_cards(source_book=source_book, subtype=subtype_like, limit=limit)
        return [str(row.get("summary") or row.get("content") or "") for row in rows]

    async def delete_card(self, card_id: str) -> bool:
        detail_path = self._detail_path(card_id)
        # 系统自动产物（auto_summary、反思产物等）默认归档而非物理删除
        detail = await self.get_card_detail(card_id)
        if detail and any(t.startswith("auto_") for t in detail.get("tags", [])):
            await self.update_card_metadata(card_id, status="archived")
            logger.info("系统产物卡片已归档（非物理删除）: %s", card_id)
            return True
        cursor = await self.conn.execute("DELETE FROM cards WHERE card_id = ?", (card_id,))
        if cursor.rowcount:
            if self._fts_available:
                await self.conn.execute("DELETE FROM cards_fts WHERE card_id = ?", (card_id,))
            await self.conn.execute(
                "UPDATE ledger_claims SET status = 'archived', updated_at = datetime('now') WHERE claim_id = ?",
                (card_id,),
            )
            await self.conn.execute(
                "UPDATE ledger_metrics SET status = 'archived' WHERE metric_id = ?",
                (f"metric_{card_id}",),
            )
            await self.conn.execute(
                "UPDATE ledger_evidence SET status = 'archived' WHERE evidence_id = ?",
                (f"evidence_{card_id}",),
            )
            await self.conn.execute(
                "UPDATE ledger_passages SET status = 'archived' WHERE passage_id = ?",
                (f"passage_{card_id}",),
            )
            await self.conn.execute(
                "UPDATE ledger_relations SET status = 'archived' WHERE source_id = ? OR target_id = ?",
                (card_id, card_id),
            )
            await self.conn.execute(
                """INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at)
                   VALUES (?, ?, 'CLAIM_TOMBSTONED', ?, 'PENDING', datetime('now'))
                   ON CONFLICT(event_id) DO UPDATE SET status='PENDING', applied_at=NULL""",
                (f"claim_tombstoned:{card_id}", card_id, json.dumps({"claim_id": card_id}, ensure_ascii=False)),
            )
            await self.conn.execute(
                "INSERT OR REPLACE INTO ledger_tombstones(artifact_id, artifact_type, reason, created_at) VALUES (?, 'claim', 'card_deleted', datetime('now'))",
                (card_id,),
            )
            # 清理指向已删除卡片的关系边，避免留下不可解析的图边。
            await self.conn.execute(
                "DELETE FROM card_relations WHERE source_card_id = ? OR target_card_id = ?",
                (card_id, card_id),
            )
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
        for field, value in changes.items():
            detail[field] = value
        serialized = {"book_type_ids", "source_anchor", "valid_places", "related_card_ids", "tags"}
        assignments: list[str] = []
        args: list[Any] = []
        for key, value in changes.items():
            assignments.append(f"{key} = ?")
            args.append(json.dumps(value, ensure_ascii=False, default=str) if key in serialized else value)
        args.append(card_id)
        async with self._lock:
            await self.conn.execute(f"UPDATE cards SET {', '.join(assignments)} WHERE card_id = ?", args)
            await self._project_card_to_ledger(detail)
            await self.conn.commit()
            await asyncio.to_thread(
                self._detail_path(card_id).write_text,
                json.dumps(detail, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        return True

    # ==========================================
    # 卡片自动打分引擎
    # ==========================================

    def compute_scores(self, card: dict) -> tuple[float, float]:
        """自动计算卡片的 utility_score（有用度）和 entropy_score（冷门度）。

        utility_score: 内容越长、标签越多、有原文片段 → 越有用（0-1）
        entropy_score: 内容越独特（短/稀有标签）→ 越冷门（0-1）
        """
        content = card.get("content", "")
        tags = card.get("tags", [])
        original_fragment = card.get("original_fragment", "")

        # utility_score: 基于内容丰富度
        content_len = len(content)
        has_fragment = 1 if original_fragment else 0
        tag_density = min(len(tags) / 5.0, 1.0)
        content_richness = min(content_len / 500.0, 1.0)
        utility = round(0.3 * content_richness + 0.3 * tag_density + 0.2 * has_fragment + 0.2, 2)

        # entropy_score: 基于稀有性（短内容+少标签=高冷门度）
        length_score = 1.0 - min(content_len / 500.0, 1.0)
        tag_scarcity = 1.0 - tag_density
        entropy = round(0.5 * length_score + 0.5 * tag_scarcity, 2)

        return min(max(utility, 0.0), 1.0), min(max(entropy, 0.0), 1.0)

    async def score_and_update_card(self, card_id: str) -> bool:
        """读取卡片，计算分数，回写数据库。"""
        detail = await self.get_card_detail(card_id)
        if not detail:
            return False
        utility, entropy = self.compute_scores(detail)
        await self.update_card_metadata(card_id, utility_score=utility, entropy_score=entropy)
        return True

    async def batch_score_cards(self, source_book_id: str) -> int:
        """对指定书籍的所有卡片批量打分，返回更新数量。"""
        cards = await self.search_cards(source_book=source_book_id, limit=5000)
        count = 0
        for card in cards:
            card_id = card.get("card_id")
            if not card_id:
                continue
            utility, entropy = self.compute_scores(card)
            await self.update_card_metadata(card_id, utility_score=utility, entropy_score=entropy)
            count += 1
        return count

    async def get_category_summary(self, card_type: str, category: str = None, subtype: str = None) -> dict:
        """获取分类摘要：卡片数量、平均 utility/entropy、高频标签。"""
        params: dict[str, Any] = {"card_type": card_type, "limit": 5000}
        if category:
            params["category"] = category
        if subtype:
            params["subtype"] = subtype
        cards = await self.search_cards(**params)
        if not cards:
            return {"count": 0, "avg_utility": 0, "avg_entropy": 0, "top_tags": []}

        total_utility = sum(c.get("utility_score", 0.5) for c in cards)
        total_entropy = sum(c.get("entropy_score", 0.5) for c in cards)
        count = len(cards)

        tag_freq: dict[str, int] = {}
        for c in cards:
            for t in c.get("tags", []):
                tag_freq[t] = tag_freq.get(t, 0) + 1
        top_tags = sorted(tag_freq.items(), key=lambda x: -x[1])[:10]

        return {
            "count": count,
            "avg_utility": round(total_utility / count, 3),
            "avg_entropy": round(total_entropy / count, 3),
            "top_tags": [{"tag": t, "count": n} for t, n in top_tags],
        }

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
        await self.conn.execute(
            """INSERT INTO ledger_relations
            (relation_id, source_id, target_id, relation_type, weight, note, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', datetime('now'))
            ON CONFLICT(relation_id) DO UPDATE SET
                weight=excluded.weight, note=excluded.note, status='active'""",
            (relation_id, source_card_id, target_card_id, relation_type, float(weight), note),
        )
        await self.conn.execute(
            """INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at)
               VALUES (?, ?, 'RELATION_UPSERTED', ?, 'PENDING', datetime('now'))
               ON CONFLICT(event_id) DO UPDATE SET payload=excluded.payload, status='PENDING', applied_at=NULL""",
            (f"relation:{relation_id}", relation_id, json.dumps({"source_id": source_card_id, "target_id": target_card_id}, ensure_ascii=False)),
        )
        await self.conn.commit()
        return relation_id

    async def list_pending_ledger_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM ledger_outbox WHERE status='PENDING' ORDER BY created_at LIMIT ?",
            (max(1, min(int(limit), 500)),),
        )
        rows = []
        for row in await cursor.fetchall():
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except (TypeError, ValueError):
                item["payload"] = {}
            rows.append(item)
        return rows

    async def mark_ledger_outbox_applied(self, event_id: str) -> bool:
        cursor = await self.conn.execute(
            "UPDATE ledger_outbox SET status='APPLIED', applied_at=datetime('now') WHERE event_id=? AND status='PENDING'",
            (event_id,),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


__all__ = ["CardIndexer"]