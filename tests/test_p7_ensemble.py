"""P7 群像层回归测试:轨道滚动/账本三态回摆/派系生命周期/声纹注入/矛盾巡检。"""
from __future__ import annotations

import pytest

from core.database import DatabaseManager
from models.ensemble import FactionCard, LifeTrack, VoiceCard
from services.ensemble import EnsembleService, _relation_label


@pytest.fixture
async def svc(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "p7.db")
    await db.initialize()
    service = EnsembleService(db)
    await service.initialize()
    try:
        yield service
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_life_track_roundtrip_and_rolling(svc):
    track = LifeTrack(
        character_id="liu", project_id="p7", name="刘备",
        position="平原县令", livelihood="织席贩履之业已废,倚故人周济",
        attachments=["母亲", "义名"], resources=["人心"], debts=["公孙瓒提携之恩"],
        needs=["立足之地"], updated_chapter=3,
    )
    await svc.upsert_track(track)
    loaded = (await svc.get_tracks("p7"))[0]
    assert loaded.name == "刘备" and loaded.needs == ["立足之地"]
    # 章末滚动:轨道更新不改他人
    track.position = "徐州,领豫州牧"; track.updated_chapter = 12
    await svc.upsert_track(track)
    assert (await svc.get_tracks("p7"))[0].updated_chapter == 12


@pytest.mark.asyncio
async def test_relationship_ledger_three_states_and_revert(svc):
    # 基线:泛泛之交 vs 挚交
    await svc.set_baseline("p7", "cao", "liu", baseline=0)
    await svc.set_baseline("p7", "guan", "liu", baseline=80)
    # 大事件强制对位:曹操与刘备事件结盟(+50),关羽与刘备事件反目(-90)
    await svc.record_event_delta("p7", "cao", "liu", "ev1", +50, "共讨董卓")
    await svc.record_event_delta("p7", "guan", "liu", "ev1", -90, "误会反目")
    rels = {r["pair"]: r for r in await svc.current_relationships("p7")}
    assert rels["cao|liu"]["current"] == 50 and rels["cao|liu"]["label"] == "友善"
    assert rels["guan|liu"]["current"] == -10 and rels["guan|liu"]["label"] == "泛泛"

    # 事件清算:按余额深浅回摆
    reminders = await svc.close_event("p7", "ev1")
    assert len(reminders) == 2
    rels_after = {r["pair"]: r for r in await svc.current_relationships("p7")}
    # 浅交(c曹刘基线0):几乎全额回摆(+50结晶0,回0)
    assert rels_after["cao|liu"]["current"] == 0
    # 深交(关刘基线80,depth=0.4):事件-90结晶-36,基线44,回摆至44
    assert rels_after["guan|liu"]["current"] == 44
    assert rels_after["guan|liu"]["label"] == "友善"
    assert any("回摆" in r or "加深" in r for r in reminders)
    # 再次清算同事件无重复
    assert await svc.close_event("p7", "ev1") == []


@pytest.mark.asyncio
async def test_faction_lifecycle_and_derivation(svc):
    tracks = [
        LifeTrack(character_id="liu", project_id="p7", name="刘备", needs=["保汉室名分"]),
        LifeTrack(character_id="cao", project_id="p7", name="曹操", needs=["挟势定局"]),
        LifeTrack(character_id="xun", project_id="p7", name="荀彧", needs=["挟势定局"]),
    ]
    factions = svc.derive_factions("p7", "ev2", tracks, event_name="讨董")
    assert len(factions) == 2  # 按首要需求聚两派
    by_need = {f.name: f for f in factions}
    assert any("荀彧" in n and "曹操" in n for n in by_need)
    for f in factions:
        await svc.create_faction(f)
    active = await svc.get_factions("p7", active_only=True)
    assert len(active) == 2
    # 事件终了:派系随 close_event 解散归档
    await svc.close_event("p7", "ev2")
    assert await svc.get_factions("p7", active_only=True) == []
    archived = await svc.get_factions("p7", active_only=False)
    assert all(f.status == "dissolved" for f in archived)


@pytest.mark.asyncio
async def test_voice_injection_and_ensemble_context(svc):
    await svc.upsert_voice(VoiceCard(
        character_id="guan", project_id="p7", name="关羽",
        speech_habits="句短,自称'某',少虚词", decision_style="谋定后动",
    ))
    await svc.set_baseline("p7", "guan", "liu", baseline=90)
    await svc.create_faction(FactionCard(
        project_id="p7", event_id="ev3", name="讨董·刘关系",
        members=["guan", "liu"], stance={"liu": "共举义兵"},
    ))
    block = await svc.render_ensemble_context("p7", ["guan", "liu"], event_id="ev3")
    assert "声纹" in block and "句短" in block
    assert "guan×liu" in block and "挚交" in block
    assert "派系图" in block and "事件终了回摆" in block
    # 空数据项目返回空串
    assert await svc.render_ensemble_context("p7-empty", ["x"]) == ""


def test_check_action_vs_track():
    track = LifeTrack(
        character_id="xun", project_id="p7", name="荀彧",
        attachments=["汉室"], needs=["扶汉"],
    )
    hit = EnsembleService.check_action_vs_track(track, "荀彧亲手弃了汉室旌旗")
    assert hit and "汉室" in hit and "轨道动机" in hit
    assert EnsembleService.check_action_vs_track(track, "荀彧整理文书") is None
    # 牵挂出现但无负面动词不算
    assert EnsembleService.check_action_vs_track(track, "荀彧望向汉室旌旗") is None


def test_relation_labels():
    assert _relation_label(80) == "挚交"
    assert _relation_label(0) == "泛泛"
    assert _relation_label(-90) == "死敌"
