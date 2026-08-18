"""Skill projection and governance-owned behavior plugin tests."""
from __future__ import annotations

import pytest

from models.behavior_plugin import BehaviorPluginSpec, PluginSource, PluginStatus
from services.behavior_plugins import BehaviorPluginRegistry, sync_governed_behavior_plugins
from services.skill_governance import SkillGovernance


def _publish_plugin(governance: SkillGovernance) -> str:
    spec = BehaviorPluginSpec(
        plugin_id="learned-style",
        name="Learned style",
        description="governed plugin",
        prompt_template_id="behavior_editor_style",
        source=PluginSource.LEARNED,
    )
    candidate = governance.submit_candidate(
        "learned style",
        "这是一个足够长的学习型行为插件提示词，用于验证治理投影和版本化缓存同步机制。",
        confidence=0.9,
        artifact={"behavior_plugin": spec.model_dump(mode="json"), "gray_percent": 50},
    )
    governance.auto_review(candidate)
    governance.manual_approve(candidate, "author")
    governance.promote(candidate)
    governance.start_grayscale(candidate, 50)
    return candidate


def test_governed_behavior_projection_preserves_crafted_plugins(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        candidate = _publish_plugin(governance)
        registry = BehaviorPluginRegistry()
        crafted = registry.get("behavior_editor_style")
        assert crafted and crafted.source == PluginSource.CRAFTED
        projected = sync_governed_behavior_plugins(governance, registry)
        assert len(projected) == 1
        assert projected[0].plugin_id.startswith(f"governed_{candidate}_v1")
        assert projected[0].status == PluginStatus.GRAY
        with pytest.raises(ValueError):
            registry.set_status(projected[0].plugin_id, PluginStatus.ACTIVE)
        assert registry.get("behavior_editor_style").status == PluginStatus.ACTIVE
    finally:
        governance.close()


@pytest.mark.asyncio
async def test_full_projection_restart_idempotent_and_gap_zero(tmp_path):
    """Batch 4：FULL projection worker 重启可恢复、幂等，projection gap = 0——
    sync_full 首次投影 FULL 激活技能；以新 SkillProjectionService 实例模拟重启后
    再 sync_full → 0 变更（投影状态持久化），且每个 FULL 技能均有投影状态（gap 0）。"""
    from core.database import DatabaseManager
    from services.indexer import CardIndexer
    from services.skill_projection import SkillProjectionService

    gov = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    db = DatabaseManager(db_path=tmp_path / "tasks.db")
    await db.initialize()
    await db.conn.execute(
        """CREATE TABLE IF NOT EXISTS universal_skills (
            skill_id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
            content TEXT NOT NULL, applicability TEXT NOT NULL,
            source_cards TEXT, created_at REAL NOT NULL
        )"""
    )
    await db.conn.commit()
    indexer = CardIndexer(index_dir=tmp_path / "index")
    await indexer.initialize()
    try:
        # 发布一个 FULL 通用技能候选（走 _project_universal_skill 分支）
        cand = gov.submit_candidate(
            "宴会节奏", "按部就班推进宴会场面的通用技能提示词，用于验证全量投影。",
            confidence=0.9,
            artifact={
                "content": {"prompt": "按步骤打磨宴会场景"},
                "type": "TEMPLATE", "name": "宴会节奏",
                "applicability": "宴会", "source_cards": [],
            },
        )
        gov.auto_review(cand)
        gov.manual_approve(cand, "author")
        gov.promote(cand, operator="author")
        gov.promote_full(cand)

        # 首次投影（worker 首次运行）
        svc1 = SkillProjectionService(db, indexer, gov)
        r1 = await svc1.sync_full()
        assert r1["projected"] >= 1

        # 模拟重启：全新实例、同一持久化库 → 幂等，0 变更
        svc2 = SkillProjectionService(db, indexer, gov)
        r2 = await svc2.sync_full()
        assert r2["projected"] == 0

        # projection gap = 0：每个 rollout FULL 的激活技能都有投影状态
        conn = gov._get_conn()
        full_skills = [i for i in gov.resolve_active_skills({}) if i["rollout"] == "FULL"]
        assert full_skills, "应至少有一个 FULL 激活技能"
        for item in full_skills:
            row = conn.execute(
                "SELECT version FROM skg_projection_state WHERE projection_key=?",
                (f"governance:{item['candidate_id']}",),
            ).fetchone()
            assert row is not None and int(row["version"]) == int(item["version"])
    finally:
        await db.close()
        await indexer.close()
        gov.close()
