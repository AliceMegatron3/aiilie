"""P6 弧线模式回归测试。"""
from __future__ import annotations

import pytest

from models.narrative import ChapterOutline, PacingActuals, Volume
from services.arc_patterns import (
    BUILTIN_ARC_PATTERNS,
    apply_arc_to_volume,
    detect_arc_candidates_from_actuals,
    get_arc_pattern,
    register_arc_candidate,
)
from services.narrative_structure import NarrativeStructureService


@pytest.fixture
async def svc(tmp_path):
    from core.database import DatabaseManager

    db = DatabaseManager(db_path=tmp_path / "p6.db")
    await db.initialize()
    service = NarrativeStructureService(db)
    await service.initialize()
    try:
        yield service
    finally:
        await db.close()


def test_six_builtin_arcs_present():
    names = {p.name for p in BUILTIN_ARC_PATTERNS.values()}
    assert {"奇遇弧", "副本弧", "结伴任务弧", "复仇弧", "蜕变弧", "归乡弧"} <= names
    for p in BUILTIN_ARC_PATTERNS.values():
        assert len(p.stages) >= 3 and p.status == "active" and p.source == "crafted"


def test_derive_budget_sequence_shape_and_smoothing():
    arc = get_arc_pattern("arc_builtin_副本弧")
    budgets = arc.derive_budget_sequence(12)
    assert len(budgets) == 12
    tensions = [b.conflict_intensity for b in budgets]
    # 阶段分配:入场4→层进6→…险胜9;整体呈先升后降轮廓
    assert max(tensions) >= 8
    assert tensions[-1] < max(tensions)
    # 阶段衔接平滑:不出现相邻跳变超过4
    for a, b in zip(tensions, tensions[1:]):
        assert abs(a - b) <= 4
    # 高张力阶段 tempo 提速
    assert all(b.tempo == 7 for b in budgets if b.conflict_intensity >= 7)


@pytest.mark.asyncio
async def test_apply_arc_to_volume_creates_and_updates(svc):
    vol = await svc.save_volume(Volume(project_id="p6", title="卷一"))
    arc = get_arc_pattern("arc_builtin_奇遇弧")
    saved = await apply_arc_to_volume(svc, "p6", vol.volume_id, arc, n_chapters=6)
    assert len(saved) == 6
    loaded = await svc.get_chapters("p6", volume_id=vol.volume_id)
    assert [c.chapter_number for c in loaded] == list(range(1, 7))
    assert all(c.arc_pattern_id == arc.pattern_id for c in loaded)
    assert loaded[0].arc_stage == "困顿" and loaded[5].arc_stage == "改变"
    # 已有章的拍纲不被覆盖:预置拍纲后重套
    target = loaded[2]
    target.outline_text = "作者写的纲要"
    from services.narrative_structure import NarrativeStructureService as NS

    target.beats = NS.parse_outline("主角得剑").beats
    target.beats_confirmed = True
    await svc.save_chapter(target)
    other = get_arc_pattern("arc_builtin_蜕变弧")
    await apply_arc_to_volume(svc, "p6", vol.volume_id, other, n_chapters=6)
    reloaded = await svc.get_chapter(target.chapter_id)
    assert reloaded.beats_confirmed is True and reloaded.outline_text == "作者写的纲要"
    assert reloaded.arc_pattern_id == other.pattern_id  # 弧线与预算更新
    # 生成简报携带弧线阶段
    brief = await svc.render_generation_brief("p6", 3)
    assert "【弧线】" in brief and reloaded.arc_stage in brief


def test_detect_arc_candidates_from_actuals():
    def ch(n, tension):
        return ChapterOutline(
            project_id="p", chapter_number=n,
            actuals=PacingActuals(conflict_intensity=tension, emotion_intensity=tension),
        )

    rising_falling = [ch(i + 1, t) for i, t in enumerate([3, 4, 5, 8, 9, 6, 4])]
    cand = detect_arc_candidates_from_actuals(rising_falling)
    assert cand is not None
    assert [s.tension_target for s in cand.stages] == [5, 9, 5]  # 前段均值/峰值/后段均值
    register_arc_candidate(cand)
    assert cand.status == "candidate" and cand.source == "quantified"
    # 平坦序列不产候选
    flat = [ch(i + 1, 5) for i in range(6)]
    assert detect_arc_candidates_from_actuals(flat) is None
    # 样本不足不产候选
    assert detect_arc_candidates_from_actuals(flat[:2]) is None
