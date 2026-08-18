"""技能语义版本化测试（V0.3：过测才激活 / 禁止直接覆盖 / 可回滚）。"""
from __future__ import annotations

import pytest

from services.skill_versioning import SkillVersion, SkillVersionError, SkillVersionedSkill


def test_initial_active_and_label():
    s = SkillVersionedSkill("skill.foo", initial="1.2.0")
    assert s.active_label() == "skill.foo@1.2.0"


def test_candidate_does_not_overwrite_active():
    s = SkillVersionedSkill("skill.foo", "1.2.0")
    cand = s.propose_candidate()  # 1.3.0-candidate
    assert cand == "skill.foo@1.3.0-candidate"
    assert s.active_label() == "skill.foo@1.2.0"  # 激活版未被覆盖
    assert s.candidate_labels() == [cand]


def test_activate_requires_passing_tests():
    s = SkillVersionedSkill("skill.foo", "1.2.0")
    cand = s.propose_candidate()
    # 未过测 → 禁止激活
    with pytest.raises(SkillVersionError, match="尚未通过测试"):
        s.activate(cand)
    # 记过测 → 可激活，剥离 -candidate
    s.record_tests(cand, True)
    assert s.activate(cand) == "skill.foo@1.3.0"
    assert not s.is_active(SkillVersion.parse(cand))


def test_failed_tests_leave_active_untouched():
    s = SkillVersionedSkill("skill.foo", "1.2.0")
    cand = s.propose_candidate()
    s.record_tests(cand, False)
    assert s.can_activate(cand) is False
    with pytest.raises(SkillVersionError):
        s.activate(cand)
    assert s.active_label() == "skill.foo@1.2.0"


def test_rollback_restores_previous_active():
    s = SkillVersionedSkill("skill.foo", "1.2.0")
    cand = s.propose_candidate()
    s.record_tests(cand, True)
    s.activate(cand)
    assert s.active_label() == "skill.foo@1.3.0"
    # 失败可回滚到 1.2.0
    assert s.rollback() == "skill.foo@1.2.0"
    assert s.rollback_chain() == []  # 已全部回滚


def test_rollback_chain_replayable():
    s = SkillVersionedSkill("skill.foo", "1.0.0")
    c1 = s.propose_candidate("minor")
    s.record_tests(c1, True)
    s.activate(c1)
    c2 = s.propose_candidate("minor")
    s.record_tests(c2, True)
    s.activate(c2)
    assert s.rollback_chain() == ["skill.foo@1.0.0", "skill.foo@1.1.0"]


def test_major_bump_and_parse_roundtrip():
    v = SkillVersion.parse("skill.foo@2.3.0-candidate")
    assert v.label() == "skill.foo@2.3.0-candidate"
    assert v.candidate is True
    with pytest.raises(SkillVersionError):
        SkillVersion.parse("not-a-skill-version")


def test_duplicate_propose_rejected():
    s = SkillVersionedSkill("skill.foo", "1.0.0")
    s.propose_candidate()
    with pytest.raises(SkillVersionError, match="已存在"):
        s.propose_candidate()


def test_regression_baseline_from_active_version():
    """Batch 4：从 active semantic version 自动产生回归基线——
    基线 key 由 name+active_label 确定性导出；激活版推进则基线随之推进。"""
    s = SkillVersionedSkill("skill.arc", "1.2.0")
    b = s.regression_baseline()
    assert b["skill"] == "skill.arc"
    assert b["baseline_label"] == "skill.arc@1.2.0"
    assert b["baseline_key"] == "baseline:skill.arc:skill.arc@1.2.0"
    # 激活版升级 → 基线自动推进到新激活版
    cand = s.propose_candidate("minor")
    s.record_tests(cand, True)
    s.activate(cand)
    b2 = s.regression_baseline()
    assert b2["baseline_label"] == "skill.arc@1.3.0"
    assert b2["baseline_key"] == "baseline:skill.arc:skill.arc@1.3.0"
    # 候选不改变基线（锚定激活版）
    s.propose_candidate("minor")
    assert s.regression_baseline()["baseline_label"] == "skill.arc@1.3.0"