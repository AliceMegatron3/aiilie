"""
services/narrative_structure.py — 叙事结构层引擎(阶段3,讨论稿20260816 第七章)
================================================================================
卷/章/拍三级纲要的持久化、自由文本纲要的确定性解析、节奏预算对账、
伏笔账本巡检。定位:内核契约级项目层结构资产的读写与核算引擎。

职责边界:
- 只管"作品骨架"(纲要/预算/伏笔),不碰生成(supervisor)、不碰知识面(卡片);
- 解析为确定性启发(台账原则:能用算的不用 LLM),产物带 needs_confirm,
  作者确认(决策台账#23)前 beats 不作为生成约束;
- 对账只在 actuals 登记后产出偏差报告,不干预生成(软目标,台账#24)。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.database import DatabaseManager
from models.narrative import (
    Beat,
    ChapterOutline,
    ForeshadowThread,
    PacingActuals,
    PacingBudget,
    PacingVariance,
    ThreadAuditItem,
    ThreadStatus,
    Volume,
)
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ThreadHint(BaseModel):
    """自由文本中识别出的伏笔建议(未入账,待作者确认)。"""
    description: str
    source_line: str


class OutlineParseResult(BaseModel):
    """自由文本纲要解析产物(全部待作者确认)。"""
    beats: list[Beat] = Field(default_factory=list)
    budget: PacingBudget | None = None
    thread_hints: list[ThreadHint] = Field(default_factory=list)


# ── 确定性启发词典(小而稳,可经配置扩展) ─────────────────────────

_ROLE_HINTS: dict[str, tuple[str, ...]] = {
    "combat_expert": ("战", "斗", "杀", "交手", "围攻", "追杀", "突围", "对峙"),
    "emotion_expert": ("泪", "别离", "重逢", "告白", "诀别", "悲", "心动", "争执不休"),
    "lore_expert": ("设定", "制度", "地理", "习俗", "来历", "渊源", "考据", "风物"),
    "event_expert": ("转折", "推进", "抵达", "离开", "会谈", "定计", "出发", "结盟"),
}

_CONFLICT_HIGH = ("高潮", "爆发", "激战", "血战", "决战", "掀桌", "翻脸", "图穷匕见")
_CONFLICT_MID = ("冲突", "争执", "对峙", "交锋")
_CONFLICT_LOW = ("平静", "过渡", "铺垫", "蓄力", "日常", "闲笔")
_EMOTION_HIGH = ("悲恸", "离别", "重逢", "崩溃", "大喜", "诀别", "恸")
_EMOTION_LOW = ("平淡", "克制的", "克制")
_TEMPO_FAST = ("急转", "连场", "快节奏", "一气呵成")
_TEMPO_SLOW = ("舒缓", "慢板", "静场")

_LIST_MARKER = re.compile(r"^\s*(?:[-*·•]|\d+[.、)]|[（(]\d+[)）])\s*")


def _score_dimension(text: str, high: tuple, mid: tuple, low: tuple) -> int:
    """关键词计分:高 8 / 中 6 / 低 3,多档命中取最高档,默认 5。"""
    if any(k in text for k in high):
        return 8
    if any(k in text for k in mid):
        return 6
    if any(k in text for k in low):
        return 3
    return 5


def _match_role_hint(beat_text: str) -> str | None:
    best_role, best_hits = None, 0
    for role, keywords in _ROLE_HINTS.items():
        hits = sum(1 for k in keywords if k in beat_text)
        if hits > best_hits:
            best_role, best_hits = role, hits
    return best_role


class NarrativeStructureService:
    """叙事结构资产引擎(自有表,严格隔离不污染存量业务数据)。"""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def initialize(self) -> None:
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS narrative_volumes (
                volume_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                arc_notes TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
        """)
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS narrative_chapters (
                chapter_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                volume_id TEXT,
                chapter_number INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                outline_text TEXT NOT NULL DEFAULT '',
                beats TEXT NOT NULL DEFAULT '[]',
                beats_confirmed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'OUTLINE',
                budget TEXT,
                actuals TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS narrative_threads (
                thread_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                description TEXT NOT NULL,
                planted_chapter INTEGER NOT NULL,
                deadline_chapter INTEGER,
                status TEXT NOT NULL DEFAULT 'PLANTED',
                advanced_chapters TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT ''
            )
        """)
        await self.db.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_narr_chapters_proj ON narrative_chapters (project_id, chapter_number)"
        )
        await self.db.conn.commit()
        logger.info("[NarrativeStructure] 叙事结构层三表初始化完成")

    # ── 解析(纯函数,确定性启发) ──────────────────────────────

    @staticmethod
    def parse_outline(text: str) -> OutlineParseResult:
        """自由文本 → 拍列表 + 预算建议 + 伏笔建议(全部待作者确认)。"""
        result = OutlineParseResult()
        for raw_line in (text or "").splitlines():
            line = _LIST_MARKER.sub("", raw_line).strip()
            if not line:
                continue
            if "伏笔" in line:
                result.thread_hints.append(
                    ThreadHint(description=line, source_line=raw_line.strip())
                )
            result.beats.append(
                Beat(text=line, role_hint=_match_role_hint(line))
            )
        budget = PacingBudget(
            conflict_intensity=_score_dimension(text, _CONFLICT_HIGH, _CONFLICT_MID, _CONFLICT_LOW),
            emotion_intensity=_score_dimension(text, _EMOTION_HIGH, (), _EMOTION_LOW),
            tempo=_score_dimension(text, _TEMPO_FAST, (), _TEMPO_SLOW),
            notes="解析自自由文本纲要",  # tier 默认 SOFT(台账#24:节奏为软目标)
        )
        result.budget = budget
        return result

    # ── 卷/章持久化 ──────────────────────────────────────────

    async def save_volume(self, volume: Volume) -> Volume:
        await self.db.conn.execute(
            """INSERT INTO narrative_volumes (volume_id, project_id, title, arc_notes, version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(volume_id) DO UPDATE SET title=excluded.title,
                   arc_notes=excluded.arc_notes, version=excluded.version+1,
                   updated_at=excluded.updated_at""",
            (volume.volume_id, volume.project_id, volume.title, volume.arc_notes,
             volume.version, volume.updated_at),
        )
        await self.db.conn.commit()
        return volume

    async def get_volumes(self, project_id: str) -> list[Volume]:
        cursor = await self.db.conn.execute(
            "SELECT * FROM narrative_volumes WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [Volume(**dict(zip(cols, r))) for r in rows]

    async def save_chapter(self, chapter: ChapterOutline) -> ChapterOutline:
        await self.db.conn.execute(
            """INSERT INTO narrative_chapters
               (chapter_id, project_id, volume_id, chapter_number, title, outline_text,
                beats, beats_confirmed, status, budget, actuals, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chapter_id) DO UPDATE SET
                   volume_id=excluded.volume_id, title=excluded.title,
                   outline_text=excluded.outline_text, beats=excluded.beats,
                   beats_confirmed=excluded.beats_confirmed, status=excluded.status,
                   budget=excluded.budget, actuals=excluded.actuals,
                   updated_at=excluded.updated_at""",
            (chapter.chapter_id, chapter.project_id, chapter.volume_id,
             chapter.chapter_number, chapter.title, chapter.outline_text,
             json.dumps([b.model_dump() for b in chapter.beats], ensure_ascii=False),
             1 if chapter.beats_confirmed else 0, chapter.status.value,
             chapter.budget.model_dump_json() if chapter.budget else None,
             chapter.actuals.model_dump_json() if chapter.actuals else None,
             chapter.updated_at),
        )
        await self.db.conn.commit()
        return chapter

    async def confirm_beats(self, chapter_id: str) -> ChapterOutline | None:
        chapter = await self.get_chapter(chapter_id)
        if chapter is None:
            return None
        chapter.beats_confirmed = True
        return await self.save_chapter(chapter)

    async def get_chapter(self, chapter_id: str) -> ChapterOutline | None:
        cursor = await self.db.conn.execute(
            "SELECT * FROM narrative_chapters WHERE chapter_id = ?", (chapter_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cursor.description]
        data = dict(zip(cols, row))
        return self._chapter_from_row(data)

    async def get_chapters(self, project_id: str, volume_id: str | None = None) -> list[ChapterOutline]:
        if volume_id:
            cursor = await self.db.conn.execute(
                "SELECT * FROM narrative_chapters WHERE project_id = ? AND volume_id = ? ORDER BY chapter_number",
                (project_id, volume_id),
            )
        else:
            cursor = await self.db.conn.execute(
                "SELECT * FROM narrative_chapters WHERE project_id = ? ORDER BY chapter_number",
                (project_id,),
            )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [self._chapter_from_row(dict(zip(cols, r))) for r in rows]

    @staticmethod
    def _chapter_from_row(data: dict[str, Any]) -> ChapterOutline:
        data = dict(data)
        data["beats"] = [Beat(**b) for b in json.loads(data.get("beats") or "[]")]
        data["beats_confirmed"] = bool(data.get("beats_confirmed"))
        if data.get("budget"):
            data["budget"] = PacingBudget.model_validate_json(data["budget"])
        if data.get("actuals"):
            data["actuals"] = PacingActuals.model_validate_json(data["actuals"])
        return ChapterOutline(**data)

    # ── 预算对账(软目标:只报告,不干预) ──────────────────────

    def reconcile_pacing(self, chapter: ChapterOutline) -> PacingVariance | None:
        """预算 vs 实绩 → 偏差报告;缺预算或实绩时返回 None。"""
        if chapter.budget is None or chapter.actuals is None:
            return None
        variance = PacingVariance(
            chapter_id=chapter.chapter_id,
            chapter_number=chapter.chapter_number,
            conflict_delta=chapter.actuals.conflict_intensity - chapter.budget.conflict_intensity,
            emotion_delta=chapter.actuals.emotion_intensity - chapter.budget.emotion_intensity,
            tempo_delta=chapter.actuals.tempo - chapter.budget.tempo,
            word_count=chapter.actuals.word_count,
        )
        if variance.conflict_delta <= -2:
            variance.flags.append("conflict_below_target")
        if variance.emotion_delta <= -2:
            variance.flags.append("emotion_below_target")
        if variance.tempo_delta <= -2:
            variance.flags.append("tempo_below_target")
        if chapter.budget.max_words and chapter.actuals.word_count > chapter.budget.max_words:
            variance.flags.append("word_over_budget")
        return variance

    # ── 伏笔账本 ──────────────────────────────────────────────

    async def upsert_thread(self, thread: ForeshadowThread) -> ForeshadowThread:
        await self.db.conn.execute(
            """INSERT INTO narrative_threads
               (thread_id, project_id, description, planted_chapter, deadline_chapter,
                status, advanced_chapters, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(thread_id) DO UPDATE SET description=excluded.description,
                   deadline_chapter=excluded.deadline_chapter, status=excluded.status,
                   advanced_chapters=excluded.advanced_chapters, notes=excluded.notes""",
            (thread.thread_id, thread.project_id, thread.description,
             thread.planted_chapter, thread.deadline_chapter, thread.status.value,
             json.dumps(thread.advanced_chapters), thread.notes),
        )
        await self.db.conn.commit()
        return thread

    async def advance_thread(self, thread_id: str, chapter_number: int) -> ForeshadowThread | None:
        thread = await self.get_thread(thread_id)
        if thread is None:
            return None
        if chapter_number not in thread.advanced_chapters:
            thread.advanced_chapters.append(chapter_number)
        thread.status = ThreadStatus.ADVANCED
        return await self.upsert_thread(thread)

    async def payoff_thread(self, thread_id: str) -> ForeshadowThread | None:
        thread = await self.get_thread(thread_id)
        if thread is None:
            return None
        thread.status = ThreadStatus.PAID_OFF
        return await self.upsert_thread(thread)

    async def get_thread(self, thread_id: str) -> ForeshadowThread | None:
        cursor = await self.db.conn.execute(
            "SELECT * FROM narrative_threads WHERE thread_id = ?", (thread_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cursor.description]
        data = dict(zip(cols, row))
        data["advanced_chapters"] = json.loads(data.get("advanced_chapters") or "[]")
        return ForeshadowThread(**data)

    async def list_threads(self, project_id: str, open_only: bool = True) -> list[ForeshadowThread]:
        if open_only:
            cursor = await self.db.conn.execute(
                "SELECT * FROM narrative_threads WHERE project_id = ? AND status IN ('PLANTED','ADVANCED')",
                (project_id,),
            )
        else:
            cursor = await self.db.conn.execute(
                "SELECT * FROM narrative_threads WHERE project_id = ?", (project_id,),
            )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        out = []
        for r in rows:
            data = dict(zip(cols, r))
            data["advanced_chapters"] = json.loads(data.get("advanced_chapters") or "[]")
            out.append(ForeshadowThread(**data))
        return out

    async def audit_threads(self, project_id: str, current_chapter: int) -> list[ThreadAuditItem]:
        """未回收伏笔巡检:闲置章数与超期(硬约束违约)标记。"""
        items: list[ThreadAuditItem] = []
        for t in await self.list_threads(project_id, open_only=True):
            last_active = max([t.planted_chapter] + list(t.advanced_chapters))
            items.append(
                ThreadAuditItem(
                    thread_id=t.thread_id,
                    description=t.description,
                    status=t.status,
                    planted_chapter=t.planted_chapter,
                    deadline_chapter=t.deadline_chapter,
                    chapters_idle=max(0, current_chapter - last_active),
                    overdue=bool(
                        t.deadline_chapter is not None and current_chapter > t.deadline_chapter
                    ),
                )
            )
        # 超期(硬约束违约)优先,其次闲置章数最多
        items.sort(key=lambda i: (not i.overdue, -i.chapters_idle))
        return items
