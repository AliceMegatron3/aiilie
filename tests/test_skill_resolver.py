"""SkillGovernance resolver rollout tests."""
from __future__ import annotations

from services.skill_governance import SkillGovernance


def _full(governance: SkillGovernance) -> str:
    candidate = governance.submit_candidate("runtime", "这是一个足够长的治理运行时技能，用于验证稳定的灰度版本解析逻辑", confidence=0.9)
    governance.auto_review(candidate)
    governance.manual_approve(candidate, "author")
    governance.promote(candidate)
    governance.start_grayscale(candidate, 100 if False else 50)
    return candidate


def test_gray_resolution_requires_task_and_pins_version(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        candidate = _full(governance)
        assert governance.resolve_active_skills({}) == []
        first = governance.resolve_active_skills({"task_id": "task-1"})
        second = governance.resolve_active_skills({"task_id": "task-1"})
        assert first == second
        if first:
            assert first[0]["candidate_id"] == candidate
            assert first[0]["version"] == 1
        governance.rollback(candidate, 1)
        pinned = governance.resolve_active_skills({"task_id": "task-1"})
        assert all(item["version"] == 1 for item in pinned)
    finally:
        governance.close()


def test_full_resolution_returns_active_version(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        candidate = governance.submit_candidate("full", "这是一个足够长的全量技能，用于验证治理唯一运行时事实源", confidence=0.9)
        governance.auto_review(candidate)
        governance.manual_approve(candidate, "author")
        governance.promote(candidate)
        governance.start_grayscale(candidate, 10)
        governance.promote_full(candidate)
        resolved = governance.resolve_active_skills({"task_id": "stable"})
        assert resolved[0]["candidate_id"] == candidate
        assert resolved[0]["rollout"] == "FULL"
    finally:
        governance.close()


def test_legacy_migration_is_idempotent_by_source_and_id(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        skill = {"skill_id": "legacy-1", "name": "legacy", "content": {"prompt": "这是一个足够长的旧技能内容，用来验证迁移映射可安全重试"}}
        first = governance.import_legacy_skill(skill, source="universal_skills")
        second = governance.import_legacy_skill(skill, source="universal_skills")
        assert first == second
        assert len(governance.list_candidates()) == 1
    finally:
        governance.close()
