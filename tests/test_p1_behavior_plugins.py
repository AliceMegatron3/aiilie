"""P1 行为插件回归测试(专精能手施工图批次1)。

覆盖:契约与状态机、浅知识强制 candidate、确定性验收与回退、
灰度采样确定性、出厂四件规格与模板落盘、supervisor 精修链集成。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from models.behavior_plugin import (
    BehaviorPluginSpec,
    PluginAcceptance,
    PluginSource,
    PluginStatus,
    PluginTrigger,
    TriggerContext,
)
from services.behavior_plugins import (
    BehaviorPluginRegistry,
    _builtin_specs,
    apply_polish_passes,
    behavior_plugin_registry,
)


class _FakeDispatcher:
    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls = []

    async def dispatch(self, prompt, project_id=None, override_mode=None, session_id=None, card_filters=None):
        self.calls.append(prompt)
        return self.outputs.pop(0) if self.outputs else "占"


# ── 契约与状态机 ──────────────────────────────────────────────

def test_builtin_specs_are_crafted_active_editors():
    specs = _builtin_specs()
    assert [s.plugin_id for s in specs] == [
        "behavior_editor_tempo", "behavior_editor_style",
        "behavior_editor_sensory", "behavior_editor_dialogue",
    ]
    assert all(s.kind == "editor" and s.status == PluginStatus.ACTIVE
               and s.source == PluginSource.CRAFTED for s in specs)
    orders = [s.run_order for s in specs]
    assert orders == sorted(orders), "出厂件须按 run_order 有序执行"


def test_quantified_registration_forced_to_candidate():
    reg = BehaviorPluginRegistry()
    spec = reg.register(BehaviorPluginSpec(
        plugin_id="q1", name="量化插件", prompt_template_id="behavior_editor_style",
        status=PluginStatus.ACTIVE, source=PluginSource.QUANTIFIED, gray_percent=100,
    ))
    assert spec.status == PluginStatus.CANDIDATE and spec.gray_percent == 0
    # candidate 不进入运行时
    ctx = TriggerContext(task_id="t1")
    assert all(s.plugin_id != "q1" for s in reg.active_for(ctx))


def test_gray_sampling_is_deterministic():
    reg = BehaviorPluginRegistry()
    reg.register(BehaviorPluginSpec(
        plugin_id="gray50", name="灰度件", prompt_template_id="behavior_editor_style",
        status=PluginStatus.GRAY, source=PluginSource.LEARNED, gray_percent=50,
    ), force=True)
    hits_t1 = [reg.get("gray50").gray_hit("t1") for _ in range(5)]
    assert len(set(hits_t1)) == 1, "同任务采样必须可复现"
    assert reg.get("gray50").gray_hit("t1") == reg.get("gray50").gray_hit("t1")
    # 采样比例合理性:100 个任务命中数应接近半数(确定性哈希)
    hits = sum(1 for i in range(100) if reg.get("gray50").gray_hit(f"task{i}"))
    assert 30 <= hits <= 70


def test_trigger_six_dimensions():
    trig = PluginTrigger(chapter_types=["高潮章"], emotion_keywords=["悲"])
    assert trig.matches(TriggerContext(chapter_type="高潮章", emotion_state="悲恸"))
    assert not trig.matches(TriggerContext(chapter_type="过渡章", emotion_state="悲恸"))
    assert not trig.matches(TriggerContext(chapter_type="高潮章", emotion_state="平静"))
    # 未填维度即通配
    wildcard = PluginTrigger()
    assert wildcard.matches(TriggerContext())


# ── 确定性验收与回退 ──────────────────────────────────────────

def test_acceptance_checks():
    acc = PluginAcceptance()
    before = "x" * 100
    ok, _ = acc.check(before, "y" * 120)
    assert ok
    assert acc.check(before, "")[1] == "empty_output"
    assert acc.check(before, '{"text": "' + "y" * 100 + '"}')[1] == "json_wrapped"
    assert acc.check(before, before)[1] == "identical_output"
    assert acc.check(before, "y" * 30)[1] == "too_short"
    assert acc.check(before, "y" * 300)[1] == "too_long"


@pytest.mark.asyncio
async def test_apply_polish_passes_accept_and_rollback():
    reg = BehaviorPluginRegistry()
    draft = "初稿正文。" * 20
    # 通行1产出合格修订,通行2产出过短文本→回退通行1结果
    dispatcher = _FakeDispatcher(outputs=["修订正文。" * 22, "太短"])
    final, records = await apply_polish_passes(
        draft, dispatcher, TriggerContext(task_id="t9"), registry=reg,
    )
    assert len(records) == 4  # 出厂四件全触发
    assert records[0].accepted and records[0].reason == "accepted"
    assert final == "修订正文。" * 22, "后续通行验收不过须回退到上一通过稿"
    rejected = [r for r in records if not r.accepted]
    assert rejected and all(r.reason in ("too_short", "too_long", "empty_output", "identical_output", "json_wrapped") or r.reason.startswith("error") for r in rejected)


@pytest.mark.asyncio
async def test_polish_passes_skip_without_dispatcher_or_draft():
    final, records = await apply_polish_passes("", None, TriggerContext())
    assert final == "" and records == []


def test_builtin_templates_on_disk():
    from utils.resource_path import get_resource_path

    builtin_dir = Path(get_resource_path("data/prompts/builtin"))
    for spec in _builtin_specs():
        path = builtin_dir / f"{spec.prompt_template_id}.json"
        assert path.exists(), f"缺少打磨模板 {path.name}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["category"] == "behavior_plugin"
        assert "{{ content }}" in data["template_text"]


@pytest.mark.asyncio
async def test_supervisor_chain_runs_polish_and_reports():
    from services.novel_supervisor import NovelSupervisor

    outputs = [
        "【设定稿】" + "设" * 60,
        "【动作稿】" + "动" * 62,
        "【情感稿】" + "情" * 64,
        "【情节稿】" + "节" * 66,
    ] + ["抛光稿。" * 30, "短", "抛光稿二。" * 32, "短"]
    dispatcher = _FakeDispatcher(outputs=outputs)
    sup = NovelSupervisor(indexer=None, optimization_applier=None, dispatcher=dispatcher)
    res = await sup.execute_creation(task_id="p1-sup", cmd_text="写一段场景", project_id="")
    assert res["success"] is True
    assert "polish" in res and len(res["polish"]) == 4
    # 4专家 + 4打磨 = 8次调用
    assert len(dispatcher.calls) == 8
    accepted_any = any(p["accepted"] for p in res["polish"])
    assert accepted_any, "至少一趟打磨应被接受"
    assert res["result"] != res["summary"]
