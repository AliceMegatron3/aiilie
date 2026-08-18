"""受控执行智能体 — 通用 Agent Plan 版本门测试（V0.4 计划模式核心）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanGateError, PlanStep, PlanStatus


def _make_plan():
    return AgentPlan(workspace_id="ws", identity="agent-a", goal="量化书籍")


def _steps(*tools):
    return [PlanStep(tool=t, args={"k": "v"}, description=f"run {t}") for t in tools]


def test_unapproved_plan_cannot_execute():
    plan = _make_plan()
    v1 = plan.draft("量化书籍", _steps("library.search_cards", "project.query"))
    # 未批准即执行 → 拒绝（fail-closed）
    assert plan.can_execute(v1, "author") is False
    with pytest.raises(PlanGateError):
        plan.execute(v1, "author")


def test_approved_plan_executes_only_approved_version():
    plan = _make_plan()
    v1 = plan.draft("量化书籍", _steps("library.search_cards"))
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")  # 作者批准版本 1

    steps = plan.execute(v1, "author")
    assert [s.tool for s in steps] == ["library.search_cards"]
    assert plan.status(v1) is PlanStatus.APPROVED


def test_edit_after_approval_creates_candidate_requiring_re_approval():
    plan = _make_plan()
    v1 = plan.draft("量化书籍", _steps("library.search_cards"))
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    # 编辑出新版本（新目标/工具），旧批准版本内容冻结
    v2 = plan.edit(_steps("library.search_cards", "reflection.start"), goal="扩大范围")
    # 新版本未批准 → 禁止执行
    with pytest.raises(PlanGateError):
        plan.execute(v2, "author")
    # 旧批准版本 v1 仍可执行，内容未变（执行中无法偷改目标/工具集）
    steps_v1 = plan.execute(v1, "author")
    assert [s.tool for s in steps_v1] == ["library.search_cards"]
    assert plan.snapshot(v1).goal == "量化书籍"

    # 新版本批准后即可执行且工具集随之冻结
    plan.submit_for_review(v2, "author")
    plan.approve(v2, "author")
    assert [s.tool for s in plan.execute(v2, "author")] == ["library.search_cards", "reflection.start"]


def test_agent_cannot_approve_own_plan():
    plan = _make_plan()  # identity / creator = "agent-a"，执行主体判定为 agent-a
    v1 = plan.draft("量化书籍", _steps("library.search_cards"))
    plan.submit_for_review(v1, "author")
    # agent 不能批准自己的计划（需独立 authors）
    with pytest.raises(PlanGateError):
        plan.approve(v1, "agent-a")
    # 独立作者批准后，执行主体需一致
    plan.approve(v1, "author")
    with pytest.raises(PlanGateError):
        plan.execute(v1, "agent-a")
    assert plan.can_execute(v1, "author") is True


def test_execute_actor_mismatch_rejected():
    plan = _make_plan()
    v1 = plan.draft("量化书籍", _steps("library.search_cards"))
    plan.submit_for_review(v1, "alice")
    plan.approve(v1, "alice")
    with pytest.raises(PlanGateError):
        plan.execute(v1, "bob")
    assert plan.execute(v1, "alice")


def test_required_tools_derived_and_frozen():
    plan = _make_plan()
    v1 = plan.draft("量化书籍", _steps("library.search_cards", "project.query"))
    assert set(plan.required_tools(v1)) == {"project.query", "library.search_cards"}
    # 编辑后被冻结迭代：旧版本工具集不变
    plan.edit(_steps("library.search_cards"), goal="收窄")
    assert set(plan.required_tools(v1)) == {"project.query", "library.search_cards"}


def test_rejected_can_resubmit_for_review():
    plan = _make_plan()
    v1 = plan.draft("量化书籍", _steps("library.search_cards"))
    plan.submit_for_review(v1, "author")
    plan.reject(v1, "author")
    assert plan.status(v1) is PlanStatus.REJECTED
    with pytest.raises(PlanGateError):
        plan.execute(v1, "author")
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")
    assert plan.can_execute(v1, "author")


def test_to_from_dict_roundtrip_preserves_gates():
    plan = _make_plan()
    v1 = plan.draft("量化书籍", _steps("library.search_cards"))
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")
    v2 = plan.edit(_steps("library.search_cards", "project.query"), goal="扩大范围")

    restored = AgentPlan.from_dict(plan.to_dict())
    assert restored.plan_id == plan.plan_id
    assert restored.latest_version == v2
    # 已批准版本 v1 可执行；驳回主体要求一致
    assert restored.can_execute(v1, "author") is True
    assert [s.tool for s in restored.execute(v1, "author")] == ["library.search_cards"]
    # 未批准的新版本 v2 仍被门禁拒绝
    with pytest.raises(PlanGateError):
        restored.execute(v2, "author")
    assert set(restored.required_tools(v2)) == {"project.query", "library.search_cards"}


def test_request_changes_returns_to_draft_with_note():
    plan = _make_plan()
    v1 = plan.draft("量化书籍", _steps("library.search_cards"))
    plan.submit_for_review(v1, "author")
    # agent 不能对自己发起的计划提修改意见
    with pytest.raises(PlanGateError):
        plan.request_changes(v1, "agent-a", "自改")
    # 作者提出修改 → 退回 DRAFT，记录意见，可重新提交并批准
    plan.request_changes(v1, "author", "请改用精确检索词")
    assert plan.status(v1) is PlanStatus.DRAFT
    assert plan.review_notes(v1)[0]["note"] == "请改用精确检索词"
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")
    assert plan.can_execute(v1, "author") is True
    # 序列化往返保留评审意见
    restored = AgentPlan.from_dict(plan.to_dict())
    assert restored.review_notes(v1)[0]["action"] == "request_changes"