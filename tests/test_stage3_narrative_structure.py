"""阶段3叙事结构层回归测试(讨论稿20260816 第七章)。

覆盖:数据契约持久化(卷/章/伏笔三表)、自由文本解析(确定性启发)、
节奏预算对账(软目标只报告)、伏笔账本巡检(超期硬约束违约标记)。
"""
from __future__ import annotations

import pytest

from core.database import DatabaseManager
from models.narrative import (
    ChapterOutline,
    ForeshadowThread,
    PacingActuals,
    PacingBudget,
    Volume,
)
from services.narrative_structure import NarrativeStructureService


@pytest.fixture
async def svc(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "stage3.db")
    await db.initialize()
    service = NarrativeStructureService(db)
    await service.initialize()
    try:
        yield service
    finally:
        await db.close()


# ── 解析器 ────────────────────────────────────────────────────

def test_parse_outline_beats_budget_and_thread_hints():
    text = """1. 主角抵达洛阳,见识董卓的跋扈(设定铺垫)
2. 朝堂之上激烈争执,图穷匕见
3. 埋下伏笔:王允腰间的玉玦来历不明
4. 夜里平静的独白,主角蓄力定计"""
    result = NarrativeStructureService.parse_outline(text)

    assert len(result.beats) == 4
    assert all(b.beat_id for b in result.beats)
    # 角色提示:设定行 → lore(列表标记已被剥离)
    roles = {b.text: b.role_hint for b in result.beats}
    assert roles["主角抵达洛阳,见识董卓的跋扈(设定铺垫)"] is not None
    # 预算启发:高冲突词(图穷匕见)+低冲突词(平静/蓄力)并存 → 取高档
    assert result.budget is not None
    assert result.budget.conflict_intensity == 8
    assert result.budget.tier.value == "SOFT"
    # 伏笔建议单独摘出
    assert len(result.thread_hints) == 1
    assert "玉玦" in result.thread_hints[0].description


def test_parse_outline_empty_text_gives_defaults():
    result = NarrativeStructureService.parse_outline("   \n  ")
    assert result.beats == []
    assert result.budget.conflict_intensity == 5  # 无信号 → 中性默认


# ── 持久化往返 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_volume_and_chapter_roundtrip_with_confirm(svc):
    vol = Volume(project_id="p1", title="第一卷", arc_notes="蓄力到爆发")
    await svc.save_volume(vol)
    assert [v.title for v in await svc.get_volumes("p1")] == ["第一卷"]

    chapter = ChapterOutline(
        project_id="p1", volume_id=vol.volume_id, chapter_number=3,
        title="洛阳风波", outline_text="原文",
        budget=PacingBudget(conflict_intensity=8, max_words=3000),
    )
    parsed = NarrativeStructureService.parse_outline("朝堂争执\n夜里独白")
    chapter.beats = parsed.beats
    saved = await svc.save_chapter(chapter)
    assert saved.beats_confirmed is False

    confirmed = await svc.confirm_beats(saved.chapter_id)
    assert confirmed.beats_confirmed is True
    assert confirmed.budget.conflict_intensity == 8

    loaded = await svc.get_chapter(saved.chapter_id)
    assert loaded.beats[0].text == "朝堂争执"
    assert loaded.budget.max_words == 3000

    chapters = await svc.get_chapters("p1", volume_id=vol.volume_id)
    assert [c.chapter_number for c in chapters] == [3]


# ── 预算对账 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reconcile_pacing_flags_and_deltas(svc):
    chapter = ChapterOutline(
        project_id="p1", chapter_number=5,
        budget=PacingBudget(conflict_intensity=8, emotion_intensity=6,
                            tempo=6, max_words=2000),
        actuals=PacingActuals(conflict_intensity=4, emotion_intensity=6,
                              tempo=7, word_count=2600),
    )
    variance = svc.reconcile_pacing(chapter)
    assert variance is not None
    assert variance.conflict_delta == -4
    assert "conflict_below_target" in variance.flags
    assert "word_over_budget" in variance.flags
    assert "emotion_below_target" not in variance.flags

    # 缺预算或实绩 → 不对账
    assert svc.reconcile_pacing(ChapterOutline(project_id="p1", chapter_number=6)) is None


# ── 伏笔账本 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_thread_lifecycle_and_audit(svc):
    t1 = await svc.upsert_thread(ForeshadowThread(
        project_id="p1", description="玉玦来历", planted_chapter=3, deadline_chapter=20,
    ))
    t2 = await svc.upsert_thread(ForeshadowThread(
        project_id="p1", description="义父的旧伤", planted_chapter=8,
    ))
    await svc.advance_thread(t1.thread_id, 12)

    items = await svc.audit_threads("p1", current_chapter=25)
    assert len(items) == 2
    # 超期者(玉玦,deadline 20 < 25)排最前且标记违约
    assert items[0].thread_id == t1.thread_id
    assert items[0].overdue is True
    assert items[0].chapters_idle == 13  # 最近活动章 12
    assert items[1].overdue is False
    assert items[1].chapters_idle == 17  # 埋于 8 章

    # 回收后退出巡检
    await svc.payoff_thread(t1.thread_id)
    remain = await svc.audit_threads("p1", current_chapter=26)
    assert [i.thread_id for i in remain] == [t2.thread_id]


@pytest.mark.asyncio
async def test_api_router_mounted():
    from api.api_router import api_router
    from tests.conftest import flatten_api_router

    paths = {r.path for r in flatten_api_router(api_router)}
    assert any("/narrative/" in p for p in paths)


# ── 集成B:生成简报(结构层→创作链路) ──────────────────────────

@pytest.mark.asyncio
async def test_render_generation_brief(svc):
    vol = await svc.save_volume(Volume(project_id="p9", title="卷一"))
    ch = ChapterOutline(
        project_id="p9", volume_id=vol.volume_id, chapter_number=5,
        outline_text="x", budget=PacingBudget(conflict_intensity=8, max_words=2000),
    )
    ch.beats = NarrativeStructureService.parse_outline("朝堂争执\n夜里独白").beats
    await svc.save_chapter(ch)

    # 未确认 → 拍纲不入简报(台账#23)
    brief = await svc.render_generation_brief("p9", 5)
    assert "拍纲" not in brief

    await svc.confirm_beats(ch.chapter_id)
    brief = await svc.render_generation_brief("p9", 5)
    assert "拍纲" in brief and "朝堂争执" in brief
    assert "软目标" in brief  # 预算为软提示(台账#24)
    assert "伏笔硬约束" not in brief

    # 到期伏笔 → 硬约束注入
    await svc.upsert_thread(ForeshadowThread(
        project_id="p9", description="玉玦来历", planted_chapter=3, deadline_chapter=5,
    ))
    brief = await svc.render_generation_brief("p9", 5)
    assert "伏笔硬约束" in brief and "玉玦来历" in brief

    # 无结构资产的项目 → 空串不干预
    assert await svc.render_generation_brief("p-empty", 1) == ""


@pytest.mark.asyncio
async def test_global_router_injects_brief_via_chapter_option(svc, tmp_path):
    from models.system import CommandRequest
    from services.global_router import GlobalRouter

    ch = ChapterOutline(
        project_id="p-r", chapter_number=2, outline_text="x",
        budget=PacingBudget(conflict_intensity=8),
    )
    ch.beats = NarrativeStructureService.parse_outline("粥棚冲突").beats
    await svc.confirm_beats((await svc.save_chapter(ch)).chapter_id)
    await svc.upsert_thread(ForeshadowThread(
        project_id="p-r", description="旧伤复发", planted_chapter=1, deadline_chapter=2,
    ))

    router = GlobalRouter(None, None, None, None, None, db=None)
    # db=None → 注入跳过(降级路径)
    req = CommandRequest(command="写第二章", options={"chapter_number": 2}, project_id="p-r")
    assert await router._inject_narrative_brief(req, "写第二章") == "写第二章"

    # 复用同一 db:通过服务私有字段注入(避免完整装配)
    router._db = svc.db
    enriched = await router._inject_narrative_brief(req, "写第二章")
    assert enriched.startswith("【叙事结构层·生成简报】")
    assert "粥棚冲突" in enriched and "旧伤复发" in enriched
    assert enriched.endswith("写第二章")
