"""阶段B 授权与信任边界回归测试。

覆盖：
1. 代码执行审批必须是服务端签发的一次性凭证（拒绝客户端伪造/重放/过期/身份不符）。
2. 插件 Runner 每次执行做服务端信任复核（见 test_parser_and_runner.py）。
"""
from __future__ import annotations

from core.config_manager import config_manager
from services.code_execution import CodeExecutionService, CodePolicyError
from models.code_execution import CodeTaskRequest


def _make_service():
    svc = CodeExecutionService(db=None)
    svc.workspace_root = svc.temp_root
    return svc


def _make_approved_plan(svc):
    req = CodeTaskRequest(command="compileall", target="x.py")
    plan = svc.plan(req)
    return plan


def test_forged_approval_rejected():
    svc = _make_service()
    plan = _make_approved_plan(svc)
    try:
        svc.verify_approval(plan, "client-forged-approval-id")
    except CodePolicyError as exc:
        assert "无效或已使用" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("客户端伪造的 approval_id 必须被拒绝")


def test_server_issued_approval_works_once():
    svc = _make_service()
    plan = _make_approved_plan(svc)
    approval = svc.issue_approval(plan.plan_id, identity="operator")
    approved = svc.verify_approval(plan, approval["approval_id"], identity="operator")
    assert approved.status == "APPROVED"
    # 一次性：再次使用同一凭证必须失败
    try:
        svc.verify_approval(plan, approval["approval_id"], identity="operator")
    except CodePolicyError as exc:
        assert "无效或已使用" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("一次性审批凭证不能被重放")


def test_approval_plan_mismatch_rejected():
    svc = _make_service()
    plan_a = svc.plan(CodeTaskRequest(command="compileall", target="a.py"))
    plan_b = svc.plan(CodeTaskRequest(command="compileall", target="b.py"))
    approval = svc.issue_approval(plan_a.plan_id, identity="operator")
    try:
        svc.verify_approval(plan_b, approval["approval_id"], identity="operator")
    except CodePolicyError as exc:
        assert "计划不匹配" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("审批凭证绑定计划，跨计划使用必须被拒绝")


def test_approval_identity_mismatch_rejected():
    svc = _make_service()
    plan = _make_approved_plan(svc)
    approval = svc.issue_approval(plan.plan_id, identity="admin")
    try:
        svc.verify_approval(plan, approval["approval_id"], identity="other")
    except CodePolicyError as exc:
        assert "身份" in str(exc) and "不一致" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("审批凭证绑定身份，他人使用必须被拒绝")


def test_approval_expiry_enforced():
    import time as time_mod

    svc = _make_service()
    plan = _make_approved_plan(svc)
    approval = svc.issue_approval(plan.plan_id, identity="operator", ttl_seconds=1)
    # 手动把 expires_at 拨到过去，模拟过期
    record = svc.approvals[approval["approval_id"]]
    record["expires_at"] = time_mod.time() - 10
    try:
        svc.verify_approval(plan, approval["approval_id"], identity="operator")
    except CodePolicyError as exc:
        assert "已过期" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("过期审批凭证必须被拒绝")


def test_baseline_path_rejects_traversal():
    """doc_id 含 '..' 时 safe_join 必须拒绝，防止越界写/读 baseline 文件。"""
    import pytest

    from services.document_confirmation import _baseline_path

    with pytest.raises(ValueError):
        _baseline_path("../../../etc/evil")


def test_baseline_path_keeps_normal_doc_id():
    from services.document_confirmation import _baseline_path

    path = _baseline_path("normal_doc_abc")
    assert path.name == "normal_doc_abc.json"


def test_tts_rejects_oversized_text():
    """超过单次合成长度上限的文本应被拒绝（成本/资源控制）。"""
    import pytest

    from services.tts import TTSDispatcher

    dispatcher = TTSDispatcher()
    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(dispatcher.synthesize("长" * 9000))