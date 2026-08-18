"""技能候选测试评估测试（V0.3：过测才激活 / 失败保持候选 / 可回滚）。"""
from __future__ import annotations

from services.skill_evaluator import gate_candidate, run_smoke_tests
from services.skill_versioning import SkillVersionedSkill


def test_passing_candidate_activates_without_overwriting_rollback_by_default():
    skill = SkillVersionedSkill("skill.foo", "1.2.0")
    cand = skill.propose_candidate("minor")  # 1.3.0-candidate
    ok = gate_candidate(
        skill, cand, claim="因果推理需可证伪证据", evidence_count=3,
        conditions=["对象=正式技能"], steps=["步骤1"],
    )
    assert ok is True
    assert skill.active_label() == "skill.foo@1.3.0"


def test_failing_candidate_stays_candidate_and_active_untouched():
    skill = SkillVersionedSkill("skill.foo", "1.2.0")
    cand = skill.propose_candidate("minor")
    ok = gate_candidate(skill, cand, claim="   ", evidence_count=0)  # 主张空 + 证据不足
    assert ok is False
    assert skill.can_activate(cand) is False
    assert skill.active_label() == "skill.foo@1.2.0"


def test_conditions_or_steps_missing_fails_when_declared():
    report = run_smoke_tests(claim="观点", evidence_count=1, conditions=["", "x"], steps=["步骤"])
    assert report.ok(threshold=1.0) is False
    assert not any(c.name == "conditions_present" and c.passed for c in report.cases)


def test_rollback_returns_to_previous_active():
    skill = SkillVersionedSkill("skill.foo", "1.0.0")
    c1 = skill.propose_candidate("minor")
    gate_candidate(skill, c1, claim="观点A", evidence_count=2)
    c2 = skill.propose_candidate("minor")
    gate_candidate(skill, c2, claim="观点B", evidence_count=2)
    assert skill.active_label() == "skill.foo@1.2.0"
    assert skill.rollback() == "skill.foo@1.1.0"