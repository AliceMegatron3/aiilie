"""SkillGovernance 状态与归因闭环测试。

覆盖:退休/恢复状态机 + candidate_id@version 效果归因 + 行为插件重启后从
Governance 完整重建。全部使用临时 db_path / 临时注册表,不触碰真实数据。
"""
from __future__ import annotations

import pytest

from models.behavior_plugin import PluginSource, PluginStatus
from services.behavior_plugins import (
    BehaviorPluginRegistry,
    rebuild_from_governance,
    sync_governed_behavior_plugins,
)
from services.skill_governance import (
    CAND_FULL,
    CAND_PROMOTED,
    CAND_RETIRED,
    SkillGovernance,
    SkillGovernanceError,
)


def _publish_full(governance: SkillGovernance, name: str = "closure") -> str:
    """构造候选并一路推进到全量生效(FULL)。"""
    cand = governance.submit_candidate(
        name,
        "这是一个足够长的状态闭环技能提示词,用于验证退休恢复与效果归因的完整生命周期。",
        confidence=0.9,
    )
    governance.auto_review(cand)
    governance.manual_approve(cand, "author")
    governance.promote(cand)
    governance.start_grayscale(cand, 10)
    governance.promote_full(cand)
    return cand


# ------------------------------------------------------------------ 退休/恢复


def test_retire_restore_state_machine(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        cand = _publish_full(governance)
        assert governance._get_candidate(cand)["status"] == CAND_FULL
        assert governance.resolve_active_skills({"task_id": "t"})[0]["candidate_id"] == cand

        # 退休:仅生效态可退;置 RETIRED + active version RETIRED + 审计 RETIRE
        assert governance.retire(cand, operator="system", reason="效果下滑") == CAND_RETIRED
        assert governance._get_candidate(cand)["status"] == CAND_RETIRED
        assert governance.get_active_version(cand)["active_status"] == "RETIRED"
        assert governance.resolve_active_skills({"task_id": "t"}) == []
        assert any(e["event"] == "RETIRE" for e in governance.audit_trail(cand))
        assert cand in {c["candidate_id"] for c in governance.list_retired()}

        # 恢复:仅 RETIRED 可恢复;回到 PROMOTED 并保留 active version + 审计 RESTORE
        assert governance.restore(cand, operator="system") == CAND_PROMOTED
        assert governance._get_candidate(cand)["status"] == CAND_PROMOTED
        assert governance.get_active_version(cand)["version"] == 1
        assert governance.get_active_version(cand)["active_status"] == "ACTIVE"
        assert any(e["event"] == "RESTORE" for e in governance.audit_trail(cand))
        assert cand not in {c["candidate_id"] for c in governance.list_retired()}

        # 恢复后回到已落地态 PROMOTED(运行时只解析 FULL/GRAY),重新全量后再次生效——恢复闭环
        assert governance.resolve_active_skills({"task_id": "t"}) == []
        governance.promote_full(cand)
        assert governance.resolve_active_skills({"task_id": "t"})[0]["candidate_id"] == cand
    finally:
        governance.close()


def test_retire_restore_illegal_transitions(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        cand = _publish_full(governance)
        # 未退休就 restore → 抛错
        with pytest.raises(SkillGovernanceError):
            governance.restore(cand)
        governance.retire(cand)
        # 已退休再 retire → 抛错
        with pytest.raises(SkillGovernanceError):
            governance.retire(cand)
        # 未生效(未落地)就 retire → 抛错
        pending = governance.submit_candidate(
            "pending", "这是一个足够长的未落地候选,用于验证非法退休流转拦截。", confidence=0.9
        )
        with pytest.raises(SkillGovernanceError):
            governance.retire(pending)
    finally:
        governance.close()


# ------------------------------------------------------------------ 效果归因


def test_effect_stats_by_candidate_version(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        cand = _publish_full(governance)
        governance.record_effect(cand, 1, "win_rate", 0.8, sample_size=10)
        governance.record_effect(cand, 1, "win_rate", 0.6, sample_size=8)
        governance.record_effect(cand, 1, "acceptance", 0.7, sample_size=5)
        governance.record_effect(cand, 2, "win_rate", 0.9, sample_size=6)

        stats = {s["metric"]: s for s in governance.effect_stats(cand)}
        assert stats["win_rate"]["count"] == 3
        assert stats["win_rate"]["mean"] == pytest.approx((0.8 + 0.6 + 0.9) / 3)
        assert stats["win_rate"]["sum"] == pytest.approx(0.8 + 0.6 + 0.9)
        assert stats["win_rate"]["sample_size"] == 10 + 8 + 6
        assert stats["acceptance"]["count"] == 1
        assert stats["acceptance"]["mean"] == pytest.approx(0.7)

        # 按版本限定:candidate_id@version 归因
        v1 = {s["metric"]: s for s in governance.effect_stats(cand, version=1)}
        assert v1["win_rate"]["count"] == 2
        assert v1["win_rate"]["mean"] == pytest.approx((0.8 + 0.6) / 2)
        assert v1["win_rate"]["sum"] == pytest.approx(0.8 + 0.6)
        assert "acceptance" in v1

        v2 = {s["metric"]: s for s in governance.effect_stats(cand, version=2)}
        assert v2["win_rate"]["count"] == 1
        assert v2["win_rate"]["mean"] == pytest.approx(0.9)
        assert "acceptance" not in v2
    finally:
        governance.close()


# ------------------------------------------------------------------ 行为插件重建


def _publish_legacy_plugin(governance: SkillGovernance) -> str:
    """构造一个已从 governance 落地的 legacy 行为插件(FULL)。"""
    cand = governance.submit_candidate(
        "legacy style plugin",
        "这是一个足够长的 legacy 行为插件提示词,用于验证重启后从治理源完整重建。",
        confidence=0.9,
        artifact={
            "legacy_payload": {
                "prompt": "按照如下手法打磨文稿,直接输出完整正文。\n手法:{content}",
                "type": "STYLE",
            }
        },
    )
    governance.auto_review(cand)
    governance.manual_approve(cand, "author")
    governance.promote(cand)
    governance.start_grayscale(cand, 10)
    governance.promote_full(cand)
    return cand


def test_rebuild_from_governance_restores_plugin(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        cand = _publish_legacy_plugin(governance)
        registry = BehaviorPluginRegistry()
        projected = rebuild_from_governance(governance, registry)
        assert len(projected) == 1
        spec = projected[0]
        assert spec.plugin_id == f"governed_{cand}_v1"
        assert spec.status == PluginStatus.ACTIVE
        assert spec.gray_percent == 100
        assert spec.prompt_override and "{content}" in spec.prompt_override
        # 治理投影:状态流转只能经 SkillGovernance,不依赖旧写路径
        with pytest.raises(ValueError):
            registry.set_status(spec.plugin_id, PluginStatus.GRAY)
        # 出厂件不被覆盖
        assert registry.get("behavior_editor_style").source == PluginSource.CRAFTED

        # 退休后重建 → 该插件不再进入运行时
        governance.retire(cand)
        rebuilt = rebuild_from_governance(governance, registry)
        assert rebuilt == []
        assert registry.get(f"governed_{cand}_v1") is None
    finally:
        governance.close()


def test_sync_alias_matches_rebuild(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        cand = _publish_legacy_plugin(governance)
        registry = BehaviorPluginRegistry()
        projected = sync_governed_behavior_plugins(governance, registry)
        assert projected and projected[0].plugin_id == f"governed_{cand}_v1"
        assert projected[0].status == PluginStatus.ACTIVE
    finally:
        governance.close()
