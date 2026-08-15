"""
services/novel_agent_skill_store.py — NovelAgentSkill 卡片化持久化 (第十部分)
==========================================================================
将 NovelAgentSkill 以批次2 InfoCard 形式保存：
- card_sub_type = 'novel_agent_skill'
- 完整技能 JSON 存入 payload
同时维护独立索引表用于快速查询技能列表、调用统计、effect_score、启停切换。
"""
from __future__ import annotations
import json
import logging
from typing import Any
from models.cards import InfoCard
from models.novel_agent import NovelAgentSkill
logger = logging.getLogger(__name__)
_CREATE_SKILL_TABLE = """
CREATE TABLE IF NOT EXISTS novel_agent_skills (
    skill_id         TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    skill_type       TEXT NOT NULL DEFAULT 'PATTERN',
    payload          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'ACTIVE',
    apply_count      INTEGER DEFAULT 0,
    effect_score     REAL DEFAULT 0.0,
    creator          TEXT DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
)
"""
class NovelAgentSkillStore:
    """技能卡片仓库：以批次2卡片 + 独立索引双写。"""
    def __init__(self, db, indexer=None) -> None:
        self.db = db
        self.indexer = indexer
    async def initialize(self) -> None:
        try:
            await self.db.conn.execute(_CREATE_SKILL_TABLE)
            await self.db.conn.commit()
            logger.info("[NovelAgentSkillStore] 技能索引表初始化完成")
        except Exception as exc:
            logger.error("[NovelAgentSkillStore] 初始化失败: %s", exc)
            raise
    async def save_skill(self, skill: NovelAgentSkill) -> str:
        """
        保存技能（双写）：
        1. 作为 InfoCard 写入批次2卡片库 (card_sub_type='novel_agent_skill')
        2. 写入独立技能索引表
        """
        # 1. 批次2 InfoCard 保存
        if self.indexer is not None:
            try:
                card = InfoCard(
                    card_id=skill.skill_id,
                    source_book_id="novel_agent_skill_library",
                    content=skill.content.get("content_text", "") or f"技能: {skill.name}",
                    tags=["novel_agent_skill", skill.name],
                    card_sub_type="novel_agent_skill",
                    category=getattr(skill.rag_filter, "get", lambda *a, **k: "misc")("category", "misc"),
                    payload=skill.model_dump(mode="json"),
                )
                await self.indexer.save_card(card)
            except Exception as exc:
                logger.warning("[NovelAgentSkillStore] 技能卡片写入批次2索引失败（继续落独立表）: %s", exc)
        # 2. 独立索引表
        stats = skill.effect_stats or {}
        await self.db.conn.execute(
            """INSERT INTO novel_agent_skills
               (skill_id, name, skill_type, payload, status, apply_count, effect_score, creator, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(skill_id) DO UPDATE SET
                name=excluded.name,
                skill_type=excluded.skill_type,
                payload=excluded.payload,
                status=excluded.status,
                updated_at=excluded.updated_at""",
            (
                skill.skill_id,
                skill.name,
                skill.type,
                json.dumps(skill.model_dump(mode="json"), ensure_ascii=False),
                skill.status,
                int(stats.get("apply_count", 0) or 0),
                float(stats.get("effect_score", 0.0) or 0.0),
                "system",
                skill.created_at,
                skill.updated_at if hasattr(skill, "updated_at") else skill.created_at,
            ),
        )
        await self.db.conn.commit()
        return skill.skill_id
    async def list_skills(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """技能列表，附带调用统计与 effect_score。"""
        cursor = await self.db.conn.execute(
            "SELECT * FROM novel_agent_skills ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(columns, row))
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except Exception:
                d["payload"] = {}
            results.append(d)
        return results
    async def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        cursor = await self.db.conn.execute(
            "SELECT * FROM novel_agent_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        columns = [d[0] for d in cursor.description]
        d = dict(zip(columns, row))
        try:
            d["payload"] = json.loads(d["payload"] or "{}")
        except Exception:
            d["payload"] = {}
        return d
    async def toggle_skill(self, skill_id: str, status: str) -> bool:
        """手动启用/归档指定技能。status: ACTIVE / ARCHIVED / PENDING_REVIEW。"""
        if status not in ("ACTIVE", "ARCHIVED", "PENDING_REVIEW"):
            return False
        cursor = await self.db.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM novel_agent_skills WHERE skill_id = ?)", (skill_id,)
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            return False
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await self.db.conn.execute(
            "UPDATE novel_agent_skills SET status = ?, updated_at = ? WHERE skill_id = ?",
            (status, now, skill_id),
        )
        await self.db.conn.commit()
        # 同步更新批次2卡片状态（payload 内 status）
        if self.indexer is not None:
            try:
                detail = await self.indexer.get_card_detail(skill_id)
                if detail and detail.get("payload"):
                    detail["payload"]["status"] = status
                    card = InfoCard.model_validate(detail)
                    await self.indexer.save_card(card)
            except Exception as exc:
                logger.warning("[NovelAgentSkillStore] 同步技能卡片状态失败: %s", exc)
        return True
    async def record_skill_apply(self, skill_id: str, audit_row: dict[str, Any]) -> None:
        """
        技能应用效果统计：应用次数 +1，累计 hit/ooc/token/feedback。
        """
        cursor = await self.db.conn.execute(
            "SELECT apply_count, payload FROM novel_agent_skills WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return
        apply_count = int(row[0] or 0)
        try:
            payload = json.loads(row[1] or "{}")
        except Exception:
            payload = {}
        stats = payload.get("effect_stats", {}) or {}
        stats["apply_count"] = int(stats.get("apply_count", 0) or 0) + 1
        stats["hit_sum"] = float(stats.get("hit_sum", 0.0) or 0.0) + float(audit_row.get("hit_score", 0.0) or 0.0)
        stats["ooc_sum"] = int(stats.get("ooc_sum", 0) or 0) + int(audit_row.get("ooc_warnings", 0) or 0)
        stats["total_tokens"] = int(stats.get("total_tokens", 0) or 0) + int(audit_row.get("total_tokens", 0) or 0)
        if audit_row.get("circuit_break"):
            stats["circuit_break_count"] = int(stats.get("circuit_break_count", 0) or 0) + 1
        fb = audit_row.get("user_feedback_score")
        if fb is not None:
            stats["user_feedback_count"] = int(stats.get("user_feedback_count", 0) or 0) + 1
            stats["user_feedback_sum"] = float(stats.get("user_feedback_sum", 0.0) or 0.0) + float(fb)
        # 计算 effect_score（基于命中率、token消耗、OOC告警、熔断比例）
        effect_score = self.compute_effect_score(stats)
        stats["effect_score"] = effect_score
        payload["effect_stats"] = stats
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await self.db.conn.execute(
            """UPDATE novel_agent_skills
               SET apply_count = ?, payload = ?, effect_score = ?, updated_at = ?
               WHERE skill_id = ?""",
            (apply_count + 1, json.dumps(payload, ensure_ascii=False), effect_score, now, skill_id),
        )
        await self.db.conn.commit()
    @staticmethod
    def compute_effect_score(stats: dict[str, Any]) -> float:
        """
        effect_score = 命中率 * 0.5 + 反馈分归一化 * 0.2 - OOC率 * 0.2 - 熔断率 * 0.1 - token惩罚
        范围 [0, 1]
        """
        apply_count = max(int(stats.get("apply_count", 0) or 0), 1)
        avg_hit = float(stats.get("hit_sum", 0.0) or 0.0) / apply_count
        ooc_rate = float(stats.get("ooc_sum", 0) or 0) / apply_count
        circuit_rate = float(stats.get("circuit_break_count", 0) or 0) / apply_count
        fb_count = int(stats.get("user_feedback_count", 0) or 0)
        fb_avg = (
            float(stats.get("user_feedback_sum", 0.0) or 0.0) / max(fb_count, 1)
            if fb_count > 0
            else 3.0
        )
        # token 消耗归一化（每千 token 扣 0.02，上限扣 0.1）
        avg_tokens = float(stats.get("total_tokens", 0) or 0) / apply_count
        token_penalty = min(0.1, avg_tokens / 1000 * 0.02)
        score = (
            avg_hit * 0.5
            + (fb_avg / 5.0) * 0.2
            - ooc_rate * 0.2
            - circuit_rate * 0.1
            - token_penalty
        )
        return round(max(0.0, min(1.0, score)), 4)