"""反思评审门测试（V0.3：反思需要检查清单，确定性）。"""
from __future__ import annotations

import pytest

from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_refinery import refine
from services.reflection_critique import critique_candidate, critique_from_refine_detail
from services.skill_versioning import SkillVersionedSkill


def test_satisfactory_candidate_approvable():
    report = critique_candidate(
        claim="观点", evidence_count=2, existing_similarity=0.2,
        existing_conflicts=0, test_pass_rate=0.9, prev_test_pass_rate=0.8,
    )
    assert report.passed is True
    assert report.failed_checks == []


def test_no_source_and_regression_and_duplicate_block():
    report = critique_candidate(
        claim="观点", evidence_count=0, existing_similarity=0.9,
        existing_conflicts=0, test_pass_rate=0.5, prev_test_pass_rate=0.9,
    )
    assert report.passed is False
    assert set(["evidence_sourced", "not_duplicate", "tests_not_regressed"]).issubset(report.failed_checks)


def test_conflict_and_boundary_and_rule_blocks():
    report = critique_candidate(
        claim="观点", evidence_count=1, existing_similarity=0.1,
        existing_conflicts=1, boundary_changed=True, project_rules_ok=False,
    )
    assert report.passed is False
    assert "no_conflict" in report.failed_checks
    assert "boundary_unchanged" in report.failed_checks
    assert "respects_project_rules" in report.failed_checks


@pytest.mark.asyncio
async def test_refine_detail_feeds_critique(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    skill = SkillVersionedSkill("skill.extracted", "1.0.0")
    report = await refine(
        ["科学方法强调因果推理需要可证伪的证据。"],
        ["因果推理需可证伪证据"],
        document_id="d1", claim_store=store, skills={"skill.extracted": skill},
    )
    detail = report.details[0]
    critique = critique_from_refine_detail(detail)
    assert critique.passed is True  # 有证据、非重复、无冲突、过测
    # 若历史通过率更高则判为回归（不予批准）
    regressed = critique_from_refine_detail(detail, prev_test_pass_rate=1.0)
    # tests_passed=True→通过率1.0，不回归
    assert regressed.passed is True