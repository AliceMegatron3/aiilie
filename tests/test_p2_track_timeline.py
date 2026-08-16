"""批次2回归:角色时间线状态(章号主键+故事时间注解+历史快照)。"""
from __future__ import annotations

import pytest

from core.database import DatabaseManager
from models.ensemble import LifeTrack
from services.ensemble import EnsembleService, infer_onstage_ids


@pytest.fixture
async def svc(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "b2.db")
    await db.initialize()
    service = EnsembleService(db)
    await service.initialize()
    try:
        yield service
    finally:
        await db.close()


async def _seed(svc):
    """同一角色三个章号的状态快照。"""
    for ch, position, story in [
        (1, "平原县令", "中平元年"),
        (10, "徐州,初领徐州牧", "兴平元年"),
        (30, "荆州,屯新野", "建安六年"),
    ]:
        await svc.upsert_track(LifeTrack(
            character_id="liu", project_id="p", name="刘备",
            chapter_key=ch, story_time=story, position=position,
            updated_chapter=ch, needs=["立足之地"],
        ))


# ── 多章快照共存(三元主键) ────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_chapter_snapshots_coexist(svc):
    await _seed(svc)
    history = await svc.get_track_history("p", "liu")
    assert [t.chapter_key for t in history] == [1, 10, 30]
    assert [t.position for t in history] == ["平原县令", "徐州,初领徐州牧", "荆州,屯新野"]


@pytest.mark.asyncio
async def test_track_at_chapter_interval_and_future_fallback(svc):
    await _seed(svc)
    # 精确命中
    t = await svc.get_track_at_chapter("p", "liu", 10)
    assert t.chapter_key == 10 and t.position == "徐州,初领徐州牧"
    # 区间:第15章 → 落最近的第10章快照
    t15 = await svc.get_track_at_chapter("p", "liu", 15)
    assert t15.chapter_key == 10
    # 未来章号 → 兜底到最新第30章
    t99 = await svc.get_track_at_chapter("p", "liu", 99)
    assert t99.chapter_key == 30
    # 故事内时间为展示注解,不参与排序
    assert t99.story_time == "建安六年"


@pytest.mark.asyncio
async def test_upsert_does_not_overwrite_other_chapters(svc):
    await _seed(svc)
    # 重写第10章,不回退第1/30章
    await svc.upsert_track(LifeTrack(
        character_id="liu", project_id="p", name="刘备",
        chapter_key=10, position="徐州(修订)", updated_chapter=10, needs=["保汉室名分"],
    ))
    history = await svc.get_track_history("p", "liu")
    positions = {t.chapter_key: t.position for t in history}
    assert positions[1] == "平原县令"  # 未受影响
    assert positions[10] == "徐州(修订)"  # 已更新
    assert positions[30] == "荆州,屯新野"  # 未受影响


@pytest.mark.asyncio
async def test_get_tracks_at_chapter_returns_per_char_latest(svc):
    await _seed(svc)
    await svc.upsert_track(LifeTrack(
        character_id="cao", project_id="p", name="曹操", chapter_key=5,
        position="东郡太守", updated_chapter=5, needs=["挟势"],
    ))
    # at_chapter=12:刘备落在第10章,曹操落在第5章
    tracks = await svc.get_tracks("p", at_chapter=12)
    by = {t.character_id: t.chapter_key for t in tracks}
    assert by["liu"] == 10 and by["cao"] == 5
    # 第3章时曹操尚未入场(6>3)
    tracks3 = await svc.get_tracks("p", at_chapter=3)
    assert "cao" not in [t.character_id for t in tracks3]


# ── 存量迁移:旧二元主键表自动迁为三元主键 ──────────────────────

@pytest.mark.asyncio
async def test_migration_from_legacy_two_key_table(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "b2legacy.db")
    await db.initialize()
    # 手工造旧结构表(二元主键,无 chapter_key)
    await db.conn.execute("""
        CREATE TABLE ensemble_life_tracks (
            character_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            data TEXT NOT NULL,
            updated_chapter INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (project_id, character_id)
        )
    """)
    import json

    old_track = LifeTrack(
        character_id="sun", project_id="p", name="孙坚",
        position="长沙太守", updated_chapter=3, needs=["讨董"],
    )
    await db.conn.execute(
        "INSERT INTO ensemble_life_tracks (character_id, project_id, data, updated_chapter)"
        " VALUES (?, ?, ?, ?)",
        ("sun", "p", old_track.model_dump_json(), 3),
    )
    await db.conn.commit()

    svc = EnsembleService(db)
    await svc.initialize()  # 触发迁移
    history = await svc.get_track_history("p", "sun")
    assert len(history) == 1
    assert history[0].character_id == "sun"
    assert history[0].chapter_key == 3, "旧 updated_chapter 应迁为 chapter_key"
    # 迁移后主键应为三元
    prag = await db.conn.execute("PRAGMA table_info(ensemble_life_tracks)")
    cols = [c[1] for c in await prag.fetchall()]
    assert "chapter_key" in cols
    await db.close()


# ── 在场识别(别名+章号快照) ────────────────────────────────────

def test_infer_onstage_alias_and_story_time():
    tracks = [
        LifeTrack(character_id="liu", project_id="p", name="刘备",
                  aliases=["玄德"], chapter_key=30, story_time="建安六年"),
        LifeTrack(character_id="cao", project_id="p", name="曹操",
                  aliases=["孟德"], chapter_key=30),
    ]
    assert infer_onstage_ids(tracks, "建安六年,玄德与孟德会猎") == ["liu", "cao"]
    assert infer_onstage_ids(tracks, "无人提及") == []
