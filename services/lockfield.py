"""
services/lockfield.py — 世界观锁定场引擎(阶段4,讨论稿20260816 第六章)
========================================================================
锁定场的持久化、must 集物化、版本编年史、偏离点登记。

职责边界:
- 物化 must 集按确定性排序((library_id, card_id) 字典序)——前缀缓存
  纪律(第九章建议#1):锁定场 must 集是稳定前缀,顺序确定才能命中
  DeepSeek 前缀缓存;
- 物化结果按 (field_id, version) 缓存,版本演化自动重建,生成时不再
  全库过滤(百书规模检索延迟不涨,台账#18);
- 偏离登记遵循台账#20:检测建议(PENDING)→作者确认(CONFIRMED)才入册,
  本服务不做自动检测(检测属知识反思巡检,后续批次接线)。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from core.database import DatabaseManager
from models.lockfield import (
    DivergenceRecord,
    DivergenceStatus,
    LockFieldConfig,
    LockFieldVersionEntry,
    MaterializedMustSet,
    evidence_at_least,
)

logger = logging.getLogger(__name__)


class LockFieldService:
    """世界观锁定场引擎(自有表,严格隔离不污染存量业务数据)。"""

    def __init__(self, db: DatabaseManager, indexer: Any = None) -> None:
        self.db = db
        self.indexer = indexer
        # must 集内存缓存:(field_id, version) -> MaterializedMustSet
        self._must_cache: dict[tuple[str, int], MaterializedMustSet] = {}

    async def initialize(self) -> None:
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS lockfields (
                field_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                config TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
        """)
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS lockfield_versions (
                entry_id TEXT PRIMARY KEY,
                field_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                change_type TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS lockfield_divergences (
                divergence_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                field_id TEXT,
                description TEXT NOT NULL,
                diverged_from_card_id TEXT,
                chapter_number INTEGER,
                command_context TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL
            )
        """)
        await self.db.conn.commit()
        logger.info("[LockField] 锁定场三表初始化完成")

    # ── 锁定场配置(单场+版本演化) ────────────────────────────

    async def save_field(
        self, config: LockFieldConfig,
        change_type: str = "update_locks",
        detail: dict | None = None,
    ) -> LockFieldConfig:
        """保存并版本化:同 field_id 重复保存自动递增版本并写编年史。"""
        cursor = await self.db.conn.execute(
            "SELECT version FROM lockfields WHERE field_id = ?", (config.field_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            config.version = 1
            change_type = "create"
        else:
            config.version = int(row[0]) + 1
        config.updated_at = config.updated_at  # 保留调用方时间戳语义
        await self.db.conn.execute(
            """INSERT INTO lockfields (field_id, project_id, name, config, version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(field_id) DO UPDATE SET name=excluded.name,
                   config=excluded.config, version=excluded.version,
                   updated_at=excluded.updated_at""",
            (config.field_id, config.project_id, config.name,
             config.model_dump_json(), config.version, config.updated_at),
        )
        entry = LockFieldVersionEntry(
            field_id=config.field_id, version=config.version,
            change_type=change_type,  # type: ignore[arg-type]
            detail=detail or {"locked_library_ids": config.locked_library_ids},
        )
        await self.db.conn.execute(
            """INSERT INTO lockfield_versions
               (entry_id, field_id, version, change_type, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry.entry_id, entry.field_id, entry.version, entry.change_type,
             json.dumps(entry.detail, ensure_ascii=False), entry.created_at),
        )
        await self.db.conn.commit()
        return config

    async def get_field(self, project_id: str) -> LockFieldConfig | None:
        """单场语义:每项目一个生效锁定场(台账#21)。"""
        cursor = await self.db.conn.execute(
            "SELECT config FROM lockfields WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return LockFieldConfig.model_validate_json(row[0])

    async def list_versions(self, field_id: str) -> list[LockFieldVersionEntry]:
        cursor = await self.db.conn.execute(
            "SELECT * FROM lockfield_versions WHERE field_id = ? ORDER BY version DESC",
            (field_id,),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        out = []
        for r in rows:
            data = dict(zip(cols, r))
            data["detail"] = json.loads(data.get("detail") or "{}")
            out.append(LockFieldVersionEntry(**data))
        return out

    async def rollback_to_version(self, field_id: str, target_version: int) -> LockFieldConfig | None:
        """回滚:将历史版本的配置内容存为新版本(编年史只增不改)。"""
        cursor = await self.db.conn.execute(
            "SELECT config FROM lockfields WHERE field_id = ?", (field_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        current = LockFieldConfig.model_validate_json(row[0])
        versions = await self.list_versions(field_id)
        # 锁定场配置的完整快照存在 lockfields 历史行外的版本日志 detail 中不完整,
        # 回滚采用版本日志重建:找到目标版本的 detail 重建库绑定与规则
        target_entry = next((v for v in versions if v.version == target_version), None)
        if target_entry is None:
            return None
        detail = target_entry.detail or {}
        if "locked_library_ids" in detail:
            current.locked_library_ids = list(detail["locked_library_ids"])
        if "type_rules" in detail:
            current.type_rules = current.type_rules.model_validate(detail["type_rules"])
        return await self.save_field(current, change_type="update_locks",
                                     detail={"rolled_back_from": target_version})

    # ── must 集物化 ───────────────────────────────────────────

    async def materialize_must_set(
        self, config: LockFieldConfig, force_rebuild: bool = False
    ) -> MaterializedMustSet:
        """锁定库内 hard+approved+evidence≥阈值的卡 → 物化 must 集。

        - 按 (library_id, card_id) 字典序稳定排序(前缀缓存纪律);
        - (field_id, version) 缓存,版本演化自动重建;
        - 索引器缺失或锁定库为空时返回空集(不阻断,诚实计数)。
        """
        cache_key = (config.field_id, config.version)
        if not force_rebuild and cache_key in self._must_cache:
            return self._must_cache[cache_key]

        cards: list[dict[str, Any]] = []
        if self.indexer is not None and config.locked_library_ids:
            rules = config.type_rules
            for library_id in config.locked_library_ids:
                try:
                    rows = await self.indexer.search_cards(
                        library_id=library_id,
                        status=rules.require_status,
                        rule_strength=rules.require_rule_strength,
                        limit=500,
                    )
                except Exception as exc:
                    logger.warning("[LockField] 锁定库 %s 检索失败(跳过): %s", library_id, exc)
                    continue
                for row in rows or []:
                    if evidence_at_least(str(row.get("evidence_level") or "D"), rules.min_evidence):
                        row = dict(row)
                        row["_lock_library"] = library_id
                        cards.append(row)
        # 确定性排序:前缀缓存的前提
        cards.sort(key=lambda c: (str(c.get("_lock_library") or ""), str(c.get("card_id") or "")))
        card_ids = [str(c.get("card_id") or "") for c in cards]
        digest = hashlib.sha1("\n".join(card_ids).encode("utf-8")).hexdigest()[:12]
        result = MaterializedMustSet(
            field_id=config.field_id, version=config.version,
            card_ids=card_ids, cards=cards, total=len(cards), digest=digest,
        )
        self._must_cache[cache_key] = result
        logger.info(
            "[LockField] must 集物化完成: field=%s v%d 共 %d 卡 digest=%s",
            config.field_id, config.version, result.total, result.digest,
        )
        return result

    # ── 偏离登记(检测建议+作者确认,台账#20) ──────────────────

    async def propose_divergence(self, record: DivergenceRecord) -> DivergenceRecord:
        record.status = DivergenceStatus.PENDING
        return await self._save_divergence(record)

    async def confirm_divergence(self, divergence_id: str) -> DivergenceRecord | None:
        record = await self.get_divergence(divergence_id)
        if record is None:
            return None
        record.status = DivergenceStatus.CONFIRMED
        return await self._save_divergence(record)

    async def retract_divergence(self, divergence_id: str) -> DivergenceRecord | None:
        record = await self.get_divergence(divergence_id)
        if record is None:
            return None
        record.status = DivergenceStatus.RETRACTED
        return await self._save_divergence(record)

    async def get_divergence(self, divergence_id: str) -> DivergenceRecord | None:
        cursor = await self.db.conn.execute(
            "SELECT * FROM lockfield_divergences WHERE divergence_id = ?", (divergence_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cursor.description]
        return DivergenceRecord(**dict(zip(cols, row)))

    async def list_divergences(
        self, project_id: str, status: DivergenceStatus | None = None
    ) -> list[DivergenceRecord]:
        if status is not None:
            cursor = await self.db.conn.execute(
                "SELECT * FROM lockfield_divergences WHERE project_id = ? AND status = ? ORDER BY rowid",
                (project_id, status.value),
            )
        else:
            cursor = await self.db.conn.execute(
                "SELECT * FROM lockfield_divergences WHERE project_id = ? ORDER BY rowid",
                (project_id,),
            )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [DivergenceRecord(**dict(zip(cols, r))) for r in rows]

    async def _save_divergence(self, record: DivergenceRecord) -> DivergenceRecord:
        await self.db.conn.execute(
            """INSERT INTO lockfield_divergences
               (divergence_id, project_id, field_id, description, diverged_from_card_id,
                chapter_number, command_context, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(divergence_id) DO UPDATE SET status=excluded.status,
                   description=excluded.description,
                   diverged_from_card_id=excluded.diverged_from_card_id""",
            (record.divergence_id, record.project_id, record.field_id, record.description,
             record.diverged_from_card_id, record.chapter_number, record.command_context,
             record.status.value, record.created_at),
        )
        await self.db.conn.commit()
        return record
