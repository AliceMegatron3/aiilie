"""作者审核门测试（V0.4：author_review.submit + 过测才激活 + 可回滚）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.author_review_gate import AuthorReviewGate, AuthorReviewGateError, make_author_review_submit_handler
from services.plan_executor import PlanExecutor
from services.skill_test_ledger import SkillTestLedger
from services.skill_versioning import SkillVersionedSkill


@pytest.mark.asyncio
async def test_submit_then_author_approve_activates_and_rollback(tmp_path):
    skill = SkillVersionedSkill("skill.extracted", "1.0.0")
    cand = skill.propose_candidate("minor")  # 1.1.0-candidate
    gate = AuthorReviewGate()
    gate.submit("skill.extracted", cand)

    # 未过测 → 作者不可激活
    with pytest.raises(AuthorReviewGateError, match="未过测"):
        gate.approve("author", skill, "skill.extracted")

    # 过测（record_tests=True，候选不自动激活）→ 作者批准 → 激活（入回滚栈）
    skill.record_tests(cand, True)
    gate.submit("skill.extracted", cand)
    active = gate.approve("author", skill, "skill.extracted")
    assert active == "skill.extracted@1.1.0"
    assert skill.active_label() == "skill.extracted@1.1.0"
    assert skill.rollback() == "skill.extracted@1.0.0"


@pytest.mark.asyncio
async def test_author_review_submit_runs_in_approved_plan(tmp_path):
    skill = SkillVersionedSkill("skill.extracted", "1.0.0")
    cand = skill.propose_candidate("minor")
    skill.record_tests(cand, True)  # 过测，候选保持未激活
    gate = AuthorReviewGate()

    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="提交审核")
    v1 = plan.draft(
        "提交审核",
        [PlanStep(tool="author_review.submit", args={"name": "skill.extracted", "candidate_label": cand})],
    )
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    results = await PlanExecutor().execute_plan(
        plan, version=v1, actor="author",
        handlers={"author_review.submit": make_author_review_submit_handler(gate)},
    )
    assert results[0].status == "OK"
    assert results[0].result["status"] == "UNDER_REVIEW"
    assert gate.pending() == {"skill.extracted": cand}
    # 作者随后批准 → 激活
    assert gate.approve("author", skill, "skill.extracted") == "skill.extracted@1.1.0"


@pytest.mark.asyncio
async def test_approve_async_blocks_regression(tmp_path):
    ledger = SkillTestLedger(base_dir=tmp_path)
    skill = SkillVersionedSkill("skill.extracted", "1.0.0")
    # 基线激活版全过
    await ledger.record("skill.extracted", "skill.extracted@1.0.0", passed=4, total=4)
    cand = skill.propose_candidate("minor")  # 1.1.0-candidate
    skill.record_tests(cand, True)
    await ledger.record("skill.extracted", cand, passed=3, total=4)  # 通过率 0.75 < 1.0
    gate = AuthorReviewGate()
    gate.submit("skill.extracted", cand)

    with pytest.raises(AuthorReviewGateError, match="回归门拒绝"):
        await gate.approve_async("author", skill, "skill.extracted",
                                 ledger=ledger, baseline_label="skill.extracted@1.0.0")
    # 被拒绝后：激活版未切走，待审恢复
    assert skill.active_label() == "skill.extracted@1.0.0"


@pytest.mark.asyncio
async def test_approve_async_allows_no_regression(tmp_path):
    ledger = SkillTestLedger(base_dir=tmp_path)
    skill = SkillVersionedSkill("skill.extracted", "1.0.0")
    await ledger.record("skill.extracted", "skill.extracted@1.0.0", passed=4, total=4)
    cand = skill.propose_candidate("minor")
    skill.record_tests(cand, True)
    await ledger.record("skill.extracted", cand, passed=4, total=4)  # 不跌破基线
    gate = AuthorReviewGate()
    gate.submit("skill.extracted", cand)
    assert await gate.approve_async("author", skill, "skill.extracted",
                                    ledger=ledger, baseline_label="skill.extracted@1.0.0") == "skill.extracted@1.1.0"