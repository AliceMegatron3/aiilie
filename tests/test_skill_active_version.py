"""SkillGovernance active version runtime tests."""
from __future__ import annotations

from services.skill_governance import SkillGovernance, CAND_AUTO_APPROVED, CAND_MANUAL_APPROVED


def test_promote_and_rollback_update_active_version(tmp_path):
    governance = SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)
    try:
        candidate = governance.submit_candidate("tempo", "这是一个足够长的节奏技能提示词内容，用于验证版本晋升和回滚逻辑", confidence=0.9)
        assert governance.auto_review(candidate) == CAND_AUTO_APPROVED
        assert governance.manual_approve(candidate, "author") == CAND_MANUAL_APPROVED
        assert governance.promote(candidate) == 1
        assert governance.get_active_version(candidate)["version"] == 1
        version = governance.rollback(candidate, 1)
        assert version == 2
        assert governance.get_active_version(candidate)["version"] == 2
    finally:
        governance.close()
