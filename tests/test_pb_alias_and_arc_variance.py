"""批次B回归:别名在场识别 + 弧线配方偏差报告 + 预览端点。"""
from __future__ import annotations

import pytest

from core.database import DatabaseManager
from models.ensemble import LifeTrack
from models.narrative import ChapterOutline, PacingActuals, PacingBudget, Volume
from services.arc_patterns import apply_arc_to_volume, get_arc_pattern
from services.ensemble import infer_onstage_ids
from services.narrative_structure import NarrativeStructureService


@pytest.fixture
async def svc(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "pb.db")
    await db.initialize()
    service = NarrativeStructureService(db)
    await service.initialize()
    try:
        yield service
    finally:
        await db.close()


# ── 别名识别 ──────────────────────────────────────────────────

def test_alias_matching_and_dedup():
    tracks = [
        LifeTrack(character_id="liu", project_id="p", name="刘备", aliases=["玄德", "刘玄德"]),
        LifeTrack(character_id="cao", project_id="p", name="曹操", aliases=["孟德"]),
        LifeTrack(character_id="guan", project_id="p", name="关羽", aliases=["云长"]),
    ]
    # 别名命中
    assert infer_onstage_ids(tracks, "玄德与孟德对饮") == ["liu", "cao"]
    # 规范名与别名同时出现只计一次,顺序按轨道卡确定
    assert infer_onstage_ids(tracks, "刘备(玄德)独行") == ["liu"]
    # 无命中
    assert infer_onstage_ids(tracks, "吕布夜袭") == []
    # 无别名字段的旧数据不报错
    legacy = [LifeTrack(character_id="x", project_id="p", name="张三")]
    assert infer_onstage_ids(legacy, "张三来了") == ["x"]


# ── 弧线偏差报告 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_arc_variance_report_math(svc):
    vol = await svc.save_volume(Volume(project_id="pb", title="卷一"))
    arc = get_arc_pattern("arc_builtin_副本弧")
    await apply_arc_to_volume(svc, "pb", vol.volume_id, arc, n_chapters=6)

    chapters = await svc.get_chapters("pb", volume_id=vol.volume_id)
    assert all(c.arc_stage for c in chapters)

    # 前两章登记实绩:一章达成,一章明显低于目标
    first, second = chapters[0], chapters[1]
    first.actuals = PacingActuals(conflict_intensity=first.budget.conflict_intensity)
    await svc.save_chapter(first)
    second.actuals = PacingActuals(conflict_intensity=max(0, second.budget.conflict_intensity - 4))
    await svc.save_chapter(second)

    report = await svc.arc_variance_report("pb", vol.volume_id)
    assert report["bound_chapters"] == 6
    assert report["measured_chapters"] == 2
    assert report["achievement_rate"] == 0.5
    flags = {i["chapter_number"]: i["flag"] for i in report["items"]}
    assert flags[first.chapter_number] == "on_target"
    assert flags[second.chapter_number] == "below_target"
    # 报告不得修改预算
    reloaded = await svc.get_chapter(second.chapter_id)
    assert reloaded.budget.conflict_intensity == second.budget.conflict_intensity
    assert "不自动修改" in report["note"]


@pytest.mark.asyncio
async def test_arc_variance_without_actuals(svc):
    vol = await svc.save_volume(Volume(project_id="pb2", title="卷一"))
    arc = get_arc_pattern("arc_builtin_奇遇弧")
    await apply_arc_to_volume(svc, "pb2", vol.volume_id, arc, n_chapters=3)
    report = await svc.arc_variance_report("pb2", vol.volume_id)
    assert report["measured_chapters"] == 0
    assert report["achievement_rate"] is None
    assert report["items"] == []


@pytest.mark.asyncio
async def test_unbound_chapters_counted(svc):
    await svc.save_chapter(ChapterOutline(
        project_id="pb3", chapter_number=1, budget=PacingBudget(conflict_intensity=5),
    ))
    report = await svc.arc_variance_report("pb3")
    assert report["bound_chapters"] == 0 and report["unbound_chapters"] == 1


def test_preview_and_variance_endpoints_mounted():
    from api.api_router import api_router
    from tests.conftest import flatten_api_router

    paths = {r.path for r in flatten_api_router(api_router)}
    assert any(p.endswith("/preview") and "arcs" in p for p in paths)
    assert any(p.endswith("/arc-variance") for p in paths)
    assert any(p.endswith("/tracks") and "ensemble" in p for p in paths)
