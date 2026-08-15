"""
services/novel_agent_audit_store.py — 多智能体审计持久化仓库 (第十部分)
===================================================================
管理 NovelAgentExecutionAudit 的落库、查询、反馈标记，以及匿名样本导出。
- 新增 novel_agent_audits 表
- save_audit / get_audit / list_audits / update_feedback
- export_samples: 导出满足最小样本数的聚合样本供反思提取器消费
"""
from __future__ import annotations
import json
import logging
from typing import Any
from models.novel_agent import NovelAgentExecutionAudit
logger = logging.getLogger(__name__)
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS novel_agent_audits (
    audit_id            TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    project_id          TEXT DEFAULT '',
    genre               TEXT DEFAULT '',
    scenario            TEXT DEFAULT '',
    agent_calls         TEXT NOT NULL DEFAULT '[]',
    total_tokens        INTEGER DEFAULT 0,
    total_duration_ms   INTEGER DEFAULT 0,
    hit_score           REAL DEFAULT 0.0,
    ooc_warnings        INTEGER DEFAULT 0,
    circuit_break       INTEGER DEFAULT 0,
    circuit_break_reason TEXT DEFAULT '',
    used_rule_ids       TEXT NOT NULL DEFAULT '[]',
    used_skill_ids      TEXT NOT NULL DEFAULT '[]',
    user_feedback_score REAL,
    user_ooc_marked     INTEGER DEFAULT 0,
    user_comment        TEXT DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
)
"""
_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_novel_agent_audits_task
ON novel_agent_audits(task_id)
"""
class NovelAgentAuditStore:
    """审计仓库：落库 + 查询 + 反馈标记 + 样本导出。"""
    def __init__(self, db) -> None:
        self.db = db
    async def initialize(self) -> None:
        try:
            await self.db.conn.execute(_CREATE_TABLE)
            await self.db.conn.execute(_CREATE_INDEX)
            await self.db.conn.commit()
            logger.info("[NovelAgentAuditStore] 审计表初始化完成")
        except Exception as exc:
            logger.error("[NovelAgentAuditStore] 初始化失败: %s", exc)
            raise
    async def save_audit(self, audit: NovelAgentExecutionAudit) -> str:
        """保存一条审计记录（不存在则插入，存在则更新）。"""
        row = (
            audit.audit_id,
            audit.task_id,
            audit.project_id,
            audit.genre,
            audit.scenario,
            json.dumps([c.model_dump(mode="json") for c in audit.agent_calls], ensure_ascii=False),
            audit.total_tokens,
            audit.total_duration_ms,
            audit.hit_score,
            audit.ooc_warnings,
            1 if audit.circuit_break else 0,
            audit.circuit_break_reason,
            json.dumps(audit.used_rule_ids, ensure_ascii=False),
            json.dumps(audit.used_skill_ids, ensure_ascii=False),
            audit.user_feedback_score,
            1 if audit.user_ooc_marked else 0,
            audit.user_comment,
            audit.created_at,
            audit.updated_at,
        )
        await self.db.conn.execute(
            """INSERT INTO novel_agent_audits
               (audit_id, task_id, project_id, genre, scenario, agent_calls,
                total_tokens, total_duration_ms, hit_score, ooc_warnings,
                circuit_break, circuit_break_reason, used_rule_ids, used_skill_ids,
                user_feedback_score, user_ooc_marked, user_comment, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(audit_id) DO UPDATE SET
                user_feedback_score=excluded.user_feedback_score,
                user_ooc_marked=excluded.user_ooc_marked,
                user_comment=excluded.user_comment,
                updated_at=excluded.updated_at""",
            row,
        )
        await self.db.conn.commit()
        return audit.audit_id
    async def update_feedback(
        self,
        task_id: str,
        score: float | None = None,
        ooc_marked: bool = False,
        comment: str = "",
    ) -> bool:
        """
        用户对生成结果反馈打分并标记 OOC。
        按 task_id 更新最近一条审计记录。
        """
        cursor = await self.db.conn.execute(
            "SELECT audit_id FROM novel_agent_audits WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        audit_id = row[0]
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await self.db.conn.execute(
            """UPDATE novel_agent_audits
               SET user_feedback_score = COALESCE(?, user_feedback_score),
                   user_ooc_marked = ?,
                   user_comment = ?,
                   updated_at = ?
               WHERE audit_id = ?""",
            (score, 1 if ooc_marked else 0, comment, now, audit_id),
        )
        await self.db.conn.commit()
        return True
    async def get_audit_by_task(self, task_id: str) -> dict[str, Any] | None:
        cursor = await self.db.conn.execute(
            "SELECT * FROM novel_agent_audits WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_dict(row, cursor)
    async def list_audits(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        cursor = await self.db.conn.execute(
            "SELECT * FROM novel_agent_audits ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(r, cursor) for r in rows]
    async def count_audits(self) -> int:
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM novel_agent_audits")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
    @staticmethod
    def _row_to_dict(row, cursor) -> dict[str, Any]:
        columns = [d[0] for d in cursor.description]
        d = dict(zip(columns, row))
        try:
            d["agent_calls"] = json.loads(d.get("agent_calls") or "[]")
        except Exception:
            d["agent_calls"] = []
        try:
            d["used_rule_ids"] = json.loads(d.get("used_rule_ids") or "[]")
        except Exception:
            d["used_rule_ids"] = []
        try:
            d["used_skill_ids"] = json.loads(d.get("used_skill_ids") or "[]")
        except Exception:
            d["used_skill_ids"] = []
        d["circuit_break"] = bool(d.get("circuit_break"))
        d["user_ooc_marked"] = bool(d.get("user_ooc_marked"))
        return d
    async def export_samples(self, min_sample_count: int = 5) -> list[dict[str, Any]]:
        """
        导出满足最小样本数的聚合样本，供反思提取器消费。
        只读导出，不修改任何原始数据。
        """
        cursor = await self.db.conn.execute(
            "SELECT * FROM novel_agent_audits ORDER BY created_at DESC LIMIT 500"
        )
        rows = await cursor.fetchall()
        samples = [self._row_to_dict(r, cursor) for r in rows]
        if len(samples) < min_sample_count:
            return []
        return samples