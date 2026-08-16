"""P4 浅知识入池回归测试:量化技能→行为插件candidate池。"""
from __future__ import annotations

import pytest

from models.behavior_plugin import PluginSource, PluginStatus, TriggerContext
from models.reflection import UniversalSkill
from services.behavior_plugins import (
    BehaviorPluginRegistry,
    _render_pass_prompt,
    register_skill_candidates,
)


def _skill(name: str, prompt: str, skill_id: str = "abc123def456") -> UniversalSkill:
    return UniversalSkill(
        skill_id=skill_id, type="TEMPLATE", name=name,
        content={"prompt": prompt},
    )


def test_skill_candidates_forced_to_candidate_pool():
    reg = BehaviorPluginRegistry()
    skills = [
        _skill("宴会场景五步法", "按以下步骤打磨宴会场景……\n文稿:\n{content}"),
        _skill("无占位技能", "短句节奏:多用三字短句收束段落。"),
    ]
    registered = register_skill_candidates(skills, registry=reg)
    assert len(registered) == 2
    for spec in registered:
        assert spec.source == PluginSource.QUANTIFIED
        assert spec.status == PluginStatus.CANDIDATE, "浅知识永不直接上岗"
        assert spec.prompt_override and "{content}" in spec.prompt_override
    # candidate 不进入运行时
    ctx = TriggerContext(task_id="t")
    assert not any(s.source == PluginSource.QUANTIFIED for s in reg.active_for(ctx))


def test_prompt_override_rendering():
    reg = BehaviorPluginRegistry()
    spec = register_skill_candidates(
        [_skill("测试手法", "手法X。\n文稿:\n{content}")], registry=reg
    )[0]
    rendered = _render_pass_prompt(spec, "正文ABC")
    assert rendered.startswith("手法X。")
    assert "正文ABC" in rendered
    # 无占位技能被自动包裹为通用编辑指令
    spec2 = register_skill_candidates(
        [_skill("无占位", "只用三字短句。", skill_id="xyz789abc111")], registry=reg
    )[0]
    rendered2 = _render_pass_prompt(spec2, "正文DEF")
    assert "只用三字短句。" in rendered2 and "正文DEF" in rendered2


@pytest.mark.asyncio
async def test_mine_snapshot_registers_candidates(tmp_path, monkeypatch):
    """阶段2挖掘链路自动入池(接线验证,经ReflectionTrigger._mine_snapshot)。"""
    from models.reflection import OptimizationRule
    from models.task import ReflectionTask
    import services.reflection_trigger as rt
    from tests.test_stage2_learning_loop import (
        _FakeApplier,
        _FakeCollector,
        _FakeRuleExtractor,
        _FakeSkillExtractor,
        _make_trigger,
    )

    skill = _skill("埋伏笔三法", "伏笔要点……\n文稿:\n{content}", skill_id="q4skill001")
    trigger, db = await _make_trigger(
        tmp_path, monkeypatch,
        rule_ext=_FakeRuleExtractor(rules=[OptimizationRule(scope="TASK_SPLIT", confidence=0.8)]),
        skill_ext=_FakeSkillExtractor(skills=[skill]),
        applier=_FakeApplier(),
        session_id="sess_p4",
    )
    try:
        # 临时替换全局注册表,避免污染其它测试
        from services import behavior_plugins as bp

        reg = BehaviorPluginRegistry()
        monkeypatch.setattr(bp, "behavior_plugin_registry", reg)
        task = ReflectionTask(task_id="sess_p4", session_id="sess_p4", priority=7)
        await trigger.process_task(task)
        # 注册表收到candidate
        cands = [s for s in reg.list_plugins() if s.source == PluginSource.QUANTIFIED]
        assert len(cands) == 1
        assert cands[0].status == PluginStatus.CANDIDATE
    finally:
        await db.close()
