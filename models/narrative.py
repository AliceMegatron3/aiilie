"""
models/narrative.py — 叙事结构层数据契约(阶段3,讨论稿20260816 第七章)
====================================================================
长篇千章规模的"作品骨架"一等结构资产:卷/章/拍三级纲要、节奏预算、
伏笔账本。定位:内核契约级项目层数据(作者/supervisor/Analyzer 三方
共读写的契约),区别于卡片(知识面)与任务(执行面)。

设计要点:
- 纲要录入形态=自由文本+系统解析+作者确认(决策台账#23):
  ChapterOutline.outline_text 保存原文,beats 为解析产物且带
  needs_confirm 标记,作者确认前不作为生成约束;
- 节奏预算硬软分档(决策台账#24):伏笔回收=硬约束(生成时注入),
  节奏强度=软目标(提示+事后对账告警),由 tier 字段显式区分;
- 拍(Beat)是 supervisor 的分解单位(阶段1 novel_role_* 的下游契约)。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChapterStatus(str, Enum):
    OUTLINE = "OUTLINE"        # 仅纲要
    DRAFTING = "DRAFTING"      # 生成中
    REVISED = "REVISED"        # 已修订未定稿
    FINAL = "FINAL"            # 已定稿(可回流)


class ThreadStatus(str, Enum):
    PLANTED = "PLANTED"        # 已埋设
    ADVANCED = "ADVANCED"      # 已推进
    PAID_OFF = "PAID_OFF"      # 已回收
    ABANDONED = "ABANDONED"    # 作者废弃


class BudgetTier(str, Enum):
    """预算执行分档(台账#24):硬约束注入生成;软目标事后对账。"""
    HARD = "HARD"
    SOFT = "SOFT"


class PacingBudget(BaseModel):
    """单章节奏预算(作者宣告的目标,软目标+事后对账)。"""
    conflict_intensity: int = Field(default=5, ge=0, le=10, description="冲突强度目标")
    emotion_intensity: int = Field(default=5, ge=0, le=10, description="情感强度目标")
    tempo: int = Field(default=5, ge=0, le=10, description="叙事节奏目标(10=最快)")
    max_words: int | None = Field(default=None, description="字数上限")
    tier: BudgetTier = BudgetTier.SOFT
    notes: str = ""


class PacingActuals(BaseModel):
    """单章节奏实绩(来源:章级 DataCard 聚合或手工登记)。"""
    conflict_intensity: int = Field(default=5, ge=0, le=10)
    emotion_intensity: int = Field(default=5, ge=0, le=10)
    tempo: int = Field(default=5, ge=0, le=10)
    word_count: int = Field(default=0, ge=0)
    source: Literal["data_card", "manual"] = "manual"
    measured_at: str = Field(default_factory=_now)


class Beat(BaseModel):
    """拍:supervisor 的创作分解单位。"""
    beat_id: str = Field(default_factory=lambda: _gen_id("beat"))
    text: str
    role_hint: str | None = Field(default=None, description="建议专家角色(如 combat_expert)")
    pattern_hint: str | None = Field(default=None, description="叙事模式提示(如 宴会/登场/战斗)")


class ForeshadowThread(BaseModel):
    """伏笔账本条目:回收为硬约束,超期由巡检告警。"""
    thread_id: str = Field(default_factory=lambda: _gen_id("thread"))
    project_id: str
    description: str
    planted_chapter: int = Field(ge=1, description="埋设章号")
    deadline_chapter: int | None = Field(default=None, ge=1, description="回收期限章号(硬约束)")
    status: ThreadStatus = ThreadStatus.PLANTED
    advanced_chapters: list[int] = Field(default_factory=list, description="推进章号列表")
    notes: str = ""


class ChapterOutline(BaseModel):
    """章纲要:自由文本原文 + 解析产物 + 预算 + 实绩。"""
    chapter_id: str = Field(default_factory=lambda: _gen_id("ch"))
    project_id: str
    volume_id: str | None = None
    chapter_number: int = Field(ge=1)
    title: str = ""
    outline_text: str = ""
    beats: list[Beat] = Field(default_factory=list)
    beats_confirmed: bool = Field(default=False, description="作者确认前不作为生成约束(台账#23)")
    status: ChapterStatus = ChapterStatus.OUTLINE
    budget: PacingBudget | None = None
    actuals: PacingActuals | None = None
    # P6:弧线绑定(套用弧线模式派生预算时写入,生成简报携带弧线阶段)
    arc_pattern_id: str | None = None
    arc_stage: str | None = None
    updated_at: str = Field(default_factory=_now)


class Volume(BaseModel):
    """卷纲要:弧线宣告(张力曲线目标/重大节点/主题)。"""
    volume_id: str = Field(default_factory=lambda: _gen_id("vol"))
    project_id: str
    title: str = ""
    arc_notes: str = Field(default="", description="弧线自由文本宣告")
    version: int = 1
    updated_at: str = Field(default_factory=_now)


class ThreadAuditItem(BaseModel):
    """伏笔巡检产出(知识反思巡检的账本视图)。"""
    thread_id: str
    description: str
    status: ThreadStatus
    planted_chapter: int
    deadline_chapter: int | None
    chapters_idle: int = Field(description="距最近活动(埋设/推进)的章数")
    overdue: bool = Field(description="是否已过回收期限(硬约束违约)")


class PacingVariance(BaseModel):
    """单章预算对账结果。"""
    chapter_id: str
    chapter_number: int
    conflict_delta: int = Field(description="实绩-预算,正为超目标")
    emotion_delta: int
    tempo_delta: int
    word_count: int
    flags: list[str] = Field(default_factory=list, description="告警标记(如 conflict_below_target)")


# ── P6:弧线模式(多章剧情骨架,作者手法指纹的载体) ─────────────

class ArcStage(BaseModel):
    """弧线阶段:名称+张力目标(0-10)。"""
    name: str
    tension_target: int = Field(ge=0, le=10)
    notes: str = ""


class ArcPattern(BaseModel):
    """弧线模式卡:骨架(阶段序列)+变异槽位+来源。

    奇遇弧/副本弧/结伴任务弧等——每位作者的标志性长剧情起伏手法;
    起伏配比经 derive_budget_sequence 派生为整卷章级预算序列。
    """
    pattern_id: str = Field(default_factory=lambda: _gen_id("arc"))
    name: str
    description: str = ""
    stages: list[ArcStage] = Field(min_length=3)
    variation_slots: list[str] = Field(default_factory=list)
    source: str = Field(default="crafted", description="crafted手工/quantified量化挖掘")
    status: str = Field(default="active", description="candidate/gray/active/retired(同浅知识律)")

    def stage_for_chapter(self, chapter_index: int, n_chapters: int) -> ArcStage:
        """第 chapter_index 章(0基)落在哪个阶段(等比分配)。"""
        if n_chapters <= 0:
            return self.stages[0]
        idx = min(len(self.stages) - 1, chapter_index * len(self.stages) // n_chapters)
        return self.stages[idx]

    def derive_budget_sequence(self, n_chapters: int) -> list[PacingBudget]:
        """起伏配比→章级预算序列(阶段衔接章取相邻均值平滑)。"""
        budgets: list[PacingBudget] = []
        for i in range(n_chapters):
            stage = self.stage_for_chapter(i, n_chapters)
            tension = stage.tension_target
            # 阶段末章与下一阶段平滑(非最终阶段且为该阶段最后一章)
            next_stage = self.stage_for_chapter(i + 1, n_chapters) if i + 1 < n_chapters else None
            if next_stage and self.stage_for_chapter(i + 1, n_chapters).name != stage.name:
                tension = (tension + next_stage.tension_target) // 2
            budgets.append(PacingBudget(
                conflict_intensity=max(0, min(10, tension)),
                emotion_intensity=max(0, min(10, tension)),
                tempo=7 if tension >= 7 else 5,
                notes=f"{self.name}·{stage.name}阶段",
            ))
        return budgets
