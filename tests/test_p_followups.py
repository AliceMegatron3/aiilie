"""后续五项建议的回归测试:群像接线/打磨档位超时/作者信号/新端点。"""
from __future__ import annotations

import asyncio

import pytest

from core.database import DatabaseManager
from models.ensemble import LifeTrack, VoiceCard
from models.system import CommandRequest
from services.author_signals import (
    compute_retention,
    join_author_retention,
    load_author_signals,
    record_author_signal,
)
from services.behavior_plugins import (
    BehaviorPluginRegistry,
    apply_polish_passes,
    load_run_records,
    persist_run_records,
)
from services.ensemble import EnsembleService, infer_onstage_ids
from models.behavior_plugin import BehaviorPluginRunRecord, TriggerContext


# ── 1. P7群像接线:确定性在场识别 + 路由注入 ────────────────────

def test_infer_onstage_ids_deterministic():
    tracks = [
        LifeTrack(character_id="liu", project_id="p", name="刘备"),
        LifeTrack(character_id="c", project_id="p", name="曹操", needs=["挟势"]),
    ]
    text = "刘备与曹操会猎于许田,关羽按剑"
    assert infer_onstage_ids(tracks, text) == ["liu", "c"]
    assert infer_onstage_ids(tracks, "吕布袭取徐州") == []
    # 无名角色按ID匹配
    assert infer_onstage_ids([LifeTrack(character_id="zhang3", project_id="p")], "zhang3来了") == ["zhang3"]


@pytest.mark.asyncio
async def test_router_injects_brief_and_ensemble(tmp_path):
    from services.global_router import GlobalRouter
    from services.narrative_structure import (
        NarrativeStructureService,
        NarrativeStructureService as NS,
    )
    from models.narrative import ChapterOutline, ForeshadowThread

    db = DatabaseManager(db_path=tmp_path / "wire.db")
    await db.initialize()
    nsvc = NarrativeStructureService(db)
    await nsvc.initialize()
    ch = ChapterOutline(project_id="pw", chapter_number=2)
    ch.beats = NS.parse_outline("刘备夜会曹操\n共议讨董").beats
    await nsvc.confirm_beats((await nsvc.save_chapter(ch)).chapter_id)
    await nsvc.upsert_thread(ForeshadowThread(
        project_id="pw", description="衣带诏", planted_chapter=1, deadline_chapter=2,
    ))

    esvc = EnsembleService(db)
    await esvc.initialize()
    await esvc.upsert_track(LifeTrack(
        character_id="liu", project_id="pw", name="刘备", needs=["保汉名"],
    ))
    await esvc.upsert_track(LifeTrack(
        character_id="cao", project_id="pw", name="曹操", needs=["挟势"],
    ))
    await esvc.set_baseline("pw", "liu", "cao", baseline=-10)
    await esvc.upsert_voice(VoiceCard(
        character_id="cao", project_id="pw", name="曹操",
        speech_habits="短句,喜引诗", decision_style="谋定",
    ))
    try:
        router = GlobalRouter(None, None, None, None, None, db=db)
        req = CommandRequest(
            command="写第二章",
            options={"chapter_number": 2},
            project_id="pw",
        )
        enriched = await router._inject_narrative_brief(req, "写第二章")
        assert "拍纲" in enriched and "伏笔硬约束" in enriched
        assert "声纹" in enriched
        assert "曹操(cao)×刘备(liu)" in enriched  # 群像块注入(名字优先,排序确定)
        assert enriched.endswith("写第二章")
    finally:
        await db.close()


# ── 2. 打磨链档位与超时 ────────────────────────────────────────

class _ModeRecorder:
    def __init__(self, output=None, delay: float = 0.0):
        self.modes = []
        self.output = output or ("修订" * 60)
        self.delay = delay

    async def dispatch(self, prompt, override_mode=None, **kw):
        import asyncio

        self.modes.append(override_mode)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.output


@pytest.mark.asyncio
async def test_polish_uses_configured_mode_and_timeout(monkeypatch, tmp_path):
    from core.config_manager import config_manager

    orig_get = config_manager.get
    monkeypatch.setattr(
        config_manager, "get",
        lambda key, default=None: {
            "behavior_plugins.editor_mode": "rapid",
            "behavior_plugins.request_timeout": 0.05,
        }.get(key, orig_get(key, default)),
    )
    reg = BehaviorPluginRegistry()
    draft = "原稿正文。" * 30

    # 档位:编辑通行走 rapid(运维建议落地)
    rec1 = _ModeRecorder()
    final1, _ = await apply_polish_passes(draft, rec1, TriggerContext(task_id="t1"), registry=reg)
    assert all(m == "rapid" for m in rec1.modes)

    # 超时:慢调度器触发独立超时→错误回退,文本无损
    slow = _ModeRecorder(delay=0.3)
    final2, records = await apply_polish_passes(draft, slow, TriggerContext(task_id="t2"), registry=reg)
    assert final2 == draft, "超时通行必须整体回退"
    assert all(r.reason.startswith("error:") for r in records)


# ── 3. 作者信号回流 ───────────────────────────────────────────

def test_author_signal_retention_and_join(tmp_path):
    assert compute_retention("同一段文字", "同一段文字") == 1.0
    assert compute_retention("甲" * 100, "乙" * 100) == 0.0
    assert 0.0 < compute_retention("他推门进来,放下剑。", "他推门进来,先放下剑。") < 1.0

    path = tmp_path / "signals.jsonl"
    record_author_signal("t1", "生成甲" * 50, "生成甲" * 50, path=path)
    record_author_signal("t2", "生成乙" * 50, "作者重写" * 50, path=path)
    signals = load_author_signals(path=path)
    assert signals["t1"] == 1.0 and signals["t2"] == 0.0

    runs_path = tmp_path / "runs.jsonl"
    persist_run_records([
        BehaviorPluginRunRecord(plugin_id="a", task_id="t1", triggered=True, accepted=True),
        BehaviorPluginRunRecord(plugin_id="a", task_id="t2", triggered=True, accepted=True),
        BehaviorPluginRunRecord(plugin_id="a", task_id="t3", triggered=True, accepted=True),  # 无信号不计
    ], history_path=runs_path)
    joined = join_author_retention(load_run_records(history_path=runs_path), signals)
    assert joined["a"]["tasks"] == 2
    assert joined["a"]["author_retention"] == 0.5


# ── 4. 新端点挂载 ─────────────────────────────────────────────

def test_followup_endpoints_mounted():
    from api.api_router import api_router
    from tests.conftest import flatten_api_router

    paths = {r.path for r in flatten_api_router(api_router)}
    assert any(p.endswith("/author-signal") for p in paths)
    assert any(p.endswith("/arcs") and "narrative" in p for p in paths)
    assert any("apply-arc" in p for p in paths)
    assert any(p.endswith("/relationships") and "ensemble" in p for p in paths)
    assert any("close" in p and "ensemble" in p for p in paths)
