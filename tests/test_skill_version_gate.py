"""版本门接入 SkillGovernance 测试（V0.3 收口）。

覆盖三交付：
- R1 候选 content 版本化落库（语义版本独立记录、可回放、非候选拒绝）；
- R2 测试评估接入（状态机/账本/Governance 三处同步、append-only）；
- R3 作者审核后 promote 走版本门（须作者批准 + 已过测才版本化激活，fail-closed）；
- 端到端 `run_version_gated_candidate` 全链路（过测落地 / 失败保持候选）。
"""
from __future__ import annotations

import pytest

from services.skill_governance import (
    CAND_MANUAL_APPROVED,
    CAND_PROMOTED,
    SkillGovernance,
    SkillGovernanceError,
    _json_loads,
)
from services.skill_test_ledger import SkillTestLedger
from services.skill_version_gate import (
    approve_and_promote,
    evaluate_candidate,
    register_candidate,
    run_version_gated_candidate,
)
from services.skill_versioning import SkillVersionError, SkillVersionedSkill

from core.config_manager import config_manager

CLAIM = "叙事弧线应当在高潮前设置伏笔，以保证转折具备充分的逻辑铺垫与情感共鸣。"


def _fresh_governance(tmp_path) -> SkillGovernance:
    return SkillGovernance(tmp_path / "skills.db", auto_review_min_confidence=0.0)


# ------------------------------------------------------------------ R1 候选 content 版本化落库


def test_register_candidate_versioned_storage(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        cand_label = skill.propose_candidate()
        candidate_id = register_candidate(
            governance, skill, cand_label,
            claim=CLAIM, evidence_count=3, conditions=["长篇小说"], steps=["埋设伏笔", "验证转折"],
        )
        cand = governance._get_candidate(candidate_id)
        assert cand is not None
        assert cand["status"] == "AUTO_APPROVED"  # 落库后已跑规则初审
        artifact = _json_loads(cand["artifact"], {})
        assert artifact["semantic_version"] == cand_label
        assert artifact["claim"] == CLAIM
        assert artifact["evidence_count"] == 3
        assert artifact["conditions"] == ["长篇小说"]
        assert artifact["steps"] == ["埋设伏笔", "验证转折"]
    finally:
        governance.close()


def test_register_rejects_non_candidate_version(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        with pytest.raises(SkillVersionError, match="带 -candidate"):
            register_candidate(governance, skill, "skill.arc@1.2.0", claim=CLAIM, evidence_count=1)
    finally:
        governance.close()


def test_two_semantic_versions_are_distinct_records(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.0.0")
        c1 = skill.propose_candidate()          # 1.1.0-candidate
        skill.record_tests(c1, True)
        skill.activate(c1)                       # 激活 → 1.1.0
        c2 = skill.propose_candidate()          # 1.2.0-candidate（自新激活版递增）
        id1 = register_candidate(governance, skill, c1, claim=CLAIM, evidence_count=2)
        # 同内容、不同语义版本 → 独立候选记录（dedup_key=candidate_label）
        id2 = register_candidate(governance, skill, c2, claim=CLAIM, evidence_count=2)
        assert id1 != id2
        art1 = governance._get_candidate(id1)["artifact"]
        art2 = governance._get_candidate(id2)["artifact"]
        assert _json_loads(art1, {})["semantic_version"] == c1
        assert _json_loads(art2, {})["semantic_version"] == c2
    finally:
        governance.close()


# ------------------------------------------------------------------ R2 测试评估接入


@pytest.mark.asyncio
async def test_evaluate_records_three_targets(tmp_path):
    governance = _fresh_governance(tmp_path)
    ledger = SkillTestLedger(base_dir=tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        cand_label = skill.propose_candidate()
        candidate_id = register_candidate(
            governance, skill, cand_label, claim=CLAIM, evidence_count=3,
            conditions=["长篇小说"], steps=["埋设伏笔"],
        )
        report = await evaluate_candidate(
            governance, skill, cand_label, candidate_id,
            claim=CLAIM, evidence_count=3, conditions=["长篇小说"], steps=["埋设伏笔"],
            ledger=ledger,
        )
        assert report.ok()
        # 状态机
        assert skill.can_activate(cand_label) is True
        # Governance append-only 测试评估
        res = governance.test_results(candidate_id)
        assert res["latest_passed"] is True
        assert res["evaluation_count"] == 1
        assert res["pass_rate"] == 1.0
        # 账本（append-only 文件）
        assert await ledger.pass_rate(skill.name, cand_label) == 1.0
    finally:
        governance.close()


@pytest.mark.asyncio
async def test_evaluate_failure_keeps_candidate_and_gate_blocks(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        cand_label = skill.propose_candidate()
        candidate_id = register_candidate(
            governance, skill, cand_label, claim=CLAIM, evidence_count=0,
        )
        report = await evaluate_candidate(
            governance, skill, cand_label, candidate_id,
            claim=CLAIM, evidence_count=0,
        )
        assert not report.ok()  # evidence_sufficient 失败
        assert skill.can_activate(cand_label) is False
        assert skill.active_label() == "skill.arc@1.2.0"  # 激活版不受影响
        res = governance.test_results(candidate_id)
        assert res["latest_passed"] is False
        # 版本门：即使作者批准，未过测也拒绝落地（fail-closed）
        governance.manual_approve(candidate_id, "author")
        with pytest.raises(SkillGovernanceError, match="未过测试"):
            governance.promote_through_gate(candidate_id)
    finally:
        governance.close()


@pytest.mark.asyncio
async def test_test_results_append_only_replayable(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        cand_label = skill.propose_candidate()
        candidate_id = register_candidate(governance, skill, cand_label, claim=CLAIM, evidence_count=2)
        await evaluate_candidate(governance, skill, cand_label, candidate_id,
                                 claim=CLAIM, evidence_count=0)
        await evaluate_candidate(governance, skill, cand_label, candidate_id,
                                 claim=CLAIM, evidence_count=2)
        res = governance.test_results(candidate_id)
        assert res["evaluation_count"] == 2
        assert [r["passed"] for r in res["records"]] == [False, True]
        assert res["latest_passed"] is True
        # 追加不覆盖 → 可回放
        assert any(e["event"] == "TEST_EVALUATION" for e in governance.audit_trail(candidate_id))
    finally:
        governance.close()


# ------------------------------------------------------------------ R3 作者审核后 promote 走版本门


def test_promote_through_gate_requires_author_approval(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        cand_label = skill.propose_candidate()
        candidate_id = register_candidate(governance, skill, cand_label, claim=CLAIM, evidence_count=2)
        governance.record_test_results(candidate_id, passed=True, passed_count=2, total_count=2)
        # 未人工批准 → 拒绝落地
        with pytest.raises(SkillGovernanceError, match="人工批准"):
            governance.promote_through_gate(candidate_id)
        assert governance._has_versions(candidate_id) is False
    finally:
        governance.close()


def test_promote_through_gate_requires_passing_tests(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        cand_label = skill.propose_candidate()
        candidate_id = register_candidate(governance, skill, cand_label, claim=CLAIM, evidence_count=2)
        governance.manual_approve(candidate_id, "author")
        # 无测试记录 → fail-closed 拒绝
        with pytest.raises(SkillGovernanceError, match="未过测试"):
            governance.promote_through_gate(candidate_id)
        assert governance.get_active_version(candidate_id) is None
        assert governance._get_candidate(candidate_id)["status"] == CAND_MANUAL_APPROVED
    finally:
        governance.close()


def test_promote_through_gate_success_snapshots_version(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        cand_label = skill.propose_candidate()
        candidate_id = register_candidate(governance, skill, cand_label, claim=CLAIM, evidence_count=2)
        governance.record_test_results(candidate_id, passed=True, passed_count=2, total_count=2)
        version = approve_and_promote(governance, candidate_id, reviewer="author")["version"]
        assert version == 1
        assert governance._get_candidate(candidate_id)["status"] == CAND_PROMOTED
        active = governance.get_active_version(candidate_id)
        assert active is not None and active["version"] == 1
        # 快照带语义版本 + 测试摘要（可回放）
        assert active["snapshot"]["semantic_version"] == cand_label
        assert active["snapshot"]["tests"]["latest_passed"] is True
        assert any(e["event"] == "PROMOTE" for e in governance.audit_trail(candidate_id))
    finally:
        governance.close()


# ------------------------------------------------------------------ 端到端


@pytest.mark.asyncio
async def test_run_version_gated_candidate_full_loop(tmp_path):
    governance = _fresh_governance(tmp_path)
    ledger = SkillTestLedger(base_dir=tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        outcome = await run_version_gated_candidate(
            governance, skill,
            claim=CLAIM, evidence_count=3, conditions=["长篇小说"], steps=["埋设伏笔"],
            ledger=ledger, reviewer="author",
        )
        assert outcome["promoted"] is True
        assert outcome["version"] == 1
        assert outcome["candidate_label"].endswith("-candidate")
        # 状态机已激活语义版本
        assert skill.active_label() == "skill.arc@1.3.0"
        # Governance 已版本化激活 + 测试评估可回放
        cand_id = outcome["candidate_id"]
        assert governance.get_active_version(cand_id)["version"] == 1
        assert governance.test_results(cand_id)["latest_passed"] is True
        assert await ledger.pass_rate(skill.name, outcome["candidate_label"]) == 1.0
    finally:
        governance.close()


@pytest.mark.asyncio
async def test_run_version_gated_candidate_failed_tests_stays_candidate(tmp_path):
    governance = _fresh_governance(tmp_path)
    try:
        skill = SkillVersionedSkill("skill.arc", "1.2.0")
        outcome = await run_version_gated_candidate(
            governance, skill, claim=CLAIM, evidence_count=0,  # 无证据 → 冒烟失败
        )
        assert outcome["promoted"] is False
        assert outcome["status"] == "TEST_FAILED"
        assert skill.active_label() == "skill.arc@1.2.0"  # 激活版不受影响
        assert skill.candidate_labels()  # 保持候选
        # 未激活、未版本化
        assert governance.get_active_version(outcome["candidate_id"]) is None
    finally:
        governance.close()


# ------------------------------------------------------------------ API 强制走版本门


async def test_promote_api_routes_only_through_gate(tmp_path, monkeypatch):
    """Batch 4：API 的 promote 只能走 promote_through_gate——
    作者已批准但未过测试 → 409 拒绝；过测后再 promote → 200 落地并激活。"""
    import httpx

    from api.deps import get_skill_governance
    from main import create_app

    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    governance = _fresh_governance(tmp_path)
    skill = SkillVersionedSkill("skill.arc", "1.2.0")
    cand_label = skill.propose_candidate()
    candidate_id = register_candidate(
        governance, skill, cand_label, claim=CLAIM, evidence_count=2,
    )
    governance.manual_approve(candidate_id, "author")

    app = create_app()
    app.dependency_overrides[get_skill_governance] = lambda: governance

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # 已批准但未过测 → 版本门拒绝（fail-closed）
            denied = await client.post(
                f"/api/v1/reflection/skills/candidates/{candidate_id}/promote"
            )
            assert denied.status_code == 409
            assert governance.get_active_version(candidate_id) is None

            # 记录过测后 → 通过版本门落地（幂等重批不重复副作用）
            governance.record_test_results(candidate_id, passed=True, passed_count=2, total_count=2)
            ok_resp = await client.post(
                f"/api/v1/reflection/skills/candidates/{candidate_id}/promote"
            )
            assert ok_resp.status_code == 200
            assert ok_resp.json()["data"]["version"] == 1
            assert governance.get_active_version(candidate_id)["version"] == 1
    finally:
        governance.close()
        app.dependency_overrides.clear()
