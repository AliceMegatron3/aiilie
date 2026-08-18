"""受控执行智能体 — 通用 Agent Plan API（V0.4 计划模式）。

暴露计划生命周期：新建草稿 → 编辑出候选版本 → 作者审核 → 作者批准 → 门禁执行。
所有响应统一 `ok()`/`fail()` 包络；执行门（未批准/版本不符/主体不一致）返回 403。
计划持久化到文件态 store（safe_join 防路径遍历）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import verify_token
from core.response import ok
from services.agent_plan import AgentPlan, PlanGateError, PlanStep
from services.agent_plan_store import AgentPlanStore
from services.plan_audit import AuditEvent, digest_args, plan_audit_store
from services.whitelisted_tools import PermissionLevel

router = APIRouter(
    prefix="/agent/plans",
    tags=["Controlled Exec Agent"],
    dependencies=[Depends(verify_token)],
)


class StepInput(BaseModel):
    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class PlanCreateRequest(BaseModel):
    goal: str = Field(min_length=1)
    workspace_id: str = "default"
    identity: str = "author"
    steps: list[StepInput] = Field(min_length=1)


class PlanEditRequest(BaseModel):
    steps: list[StepInput] = Field(min_length=1)
    goal: str | None = None


class PlanActionRequest(BaseModel):
    version: int = Field(ge=1)
    actor: str = Field(min_length=1)
    permission: PermissionLevel = PermissionLevel.GOVERN  # 执行调用方的权限档（默认作者=GOVERN）


async def _resolve_handlers(request: Any) -> dict[str, Any]:
    """解析本宿主已接线的白名单工具 handler。

    Bootstrap 装配时挂到 `app.state.agent_tool_handlers`；未装配则退回
    `plan_tools_root.build_default_handlers()`（未配置依赖的工具 fail-closed 返回
    DENIED/DISABLED 终态，绝不伪装成 PENDING/成功）。
    """
    from services.plan_tools_root import build_default_handlers

    bound = getattr(request.app.state, "agent_tool_handlers", None)
    return dict(bound) if bound else build_default_handlers()


def _runner_event_to_audit(sink: Any, plan_id: str, version: int, actor: str):
    """把 IdempotentToolRunner 的字典审计事件映射为只追加 AuditEvent 并落库。"""

    async def _fn(event: dict[str, Any]) -> None:
        await sink.append(
            AuditEvent(
                plan_id=plan_id,
                version=version,
                actor=str(event.get("actor") or actor),
                tool=str(event.get("tool") or ""),
                step_id=str(event.get("run_id") or event.get("tool") or ""),
                action=str(event.get("action") or "run"),
                status=str(event.get("status") or "PENDING"),
                note=str(event.get("message") or ""),
                args_digest=str(event.get("digest") or ""),
            )
        )

    return _fn


def _store() -> AgentPlanStore:
    return AgentPlanStore()


# Batch 5：每步 status → 机器可读 error_code（逐步错误码契约）
_STATUS_TO_ERROR_CODE = {
    "OK": "OK",
    "DENIED": "PERMISSION_DENIED",
    "INVALID": "INVALID_ARGS",
    "TIMEOUT": "TIMEOUT",
    "RUNNING": "RUNNING",
    "CANCELLED": "CANCELLED",
    "ERROR": "HANDLER_ERROR",
}


def _http(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


@router.get("", summary="列出计划")
async def list_plans() -> dict[str, Any]:
    return ok({"plans": await _store().list_plans()}, message="计划清单已返回")


@router.post("", summary="新建计划草稿")
async def create_plan(payload: PlanCreateRequest) -> dict[str, Any]:
    plan = AgentPlan(
        workspace_id=payload.workspace_id,
        identity=payload.identity,
        goal=payload.goal,
    )
    version = plan.draft(
        payload.goal,
        [PlanStep(tool=s.tool, args=s.args, description=s.description) for s in payload.steps],
    )
    await _store().save(plan)
    return ok(
        {"plan_id": plan.plan_id, "version": version, "status": "DRAFT"},
        message="计划草稿已创建",
    )


@router.get("/{plan_id}", summary="读取计划全部版本")
async def get_plan(plan_id: str) -> dict[str, Any]:
    plan = await _store().load(plan_id)
    if plan is None:
        raise _http(404, f"计划不存在: {plan_id}")
    return ok({"plan": __dict_of(plan)}, message="计划已返回")


@router.post("/{plan_id}/edit", summary="在最新版基础上编辑出新候选版本")
async def edit_plan(plan_id: str, payload: PlanEditRequest) -> dict[str, Any]:
    plan = await _store().load(plan_id)
    if plan is None:
        raise _http(404, f"计划不存在: {plan_id}")
    version = plan.edit(
        [PlanStep(tool=s.tool, args=s.args, description=s.description) for s in payload.steps],
        goal=payload.goal,
    )
    await _store().save(plan)
    return ok({"plan_id": plan_id, "version": version, "status": "DRAFT"}, message="新候选版本已生成")


@router.post("/{plan_id}/submit", summary="提交某一版本进入作者审核")
async def submit_plan(plan_id: str, payload: PlanActionRequest) -> dict[str, Any]:
    plan = await _store().load(plan_id)
    if plan is None:
        raise _http(404, f"计划不存在: {plan_id}")
    try:
        plan.submit_for_review(payload.version, payload.actor)
    except PlanGateError as exc:
        raise _http(409, str(exc))
    await _store().save(plan)
    return ok({"plan_id": plan_id, "version": payload.version, "status": "AUTHOR_REVIEW"})


@router.post("/{plan_id}/approve", summary="作者批准某一版本")
async def approve_plan(plan_id: str, payload: PlanActionRequest) -> dict[str, Any]:
    plan = await _store().load(plan_id)
    if plan is None:
        raise _http(404, f"计划不存在: {plan_id}")
    try:
        plan.approve(payload.version, payload.actor)
    except PlanGateError as exc:
        raise _http(409, str(exc))
    await _store().save(plan)
    return ok({"plan_id": plan_id, "version": payload.version, "status": "APPROVED", "approved_by": payload.actor})


@router.post("/{plan_id}/reject", summary="驳回审核中的版本")
async def reject_plan(plan_id: str, payload: PlanActionRequest) -> dict[str, Any]:
    plan = await _store().load(plan_id)
    if plan is None:
        raise _http(404, f"计划不存在: {plan_id}")
    try:
        plan.reject(payload.version, payload.actor)
    except PlanGateError as exc:
        raise _http(409, str(exc))
    await _store().save(plan)
    return ok({"plan_id": plan_id, "version": payload.version, "status": "REJECTED"})


class PlanReviewRequest(BaseModel):
    version: int = Field(ge=1)
    actor: str = Field(min_length=1)
    note: str = ""


@router.post("/{plan_id}/request-changes", summary="作者讨论：对审核中版本提修改意见并退回 DRAFT")
async def request_plan_changes(plan_id: str, payload: PlanReviewRequest) -> dict[str, Any]:
    plan = await _store().load(plan_id)
    if plan is None:
        raise _http(404, f"计划不存在: {plan_id}")
    try:
        plan.request_changes(payload.version, payload.actor, payload.note)
    except PlanGateError as exc:
        raise _http(409, str(exc))
    await _store().save(plan)
    return ok(
        {"plan_id": plan_id, "version": payload.version, "status": "DRAFT", "notes": plan.review_notes(payload.version)},
        message="已退回 DRAFT 待修改（作者讨论）",
    )


@router.get("/{plan_id}/notes", summary="读取计划评审意见（可回放）")
async def get_plan_review_notes(plan_id: str, version: int = 1) -> dict[str, Any]:
    plan = await _store().load(plan_id)
    if plan is None:
        raise _http(404, f"计划不存在: {plan_id}")
    return ok({"plan_id": plan_id, "version": version, "notes": plan.review_notes(version)}, message="评审意见已返回")


@router.post("/{plan_id}/execute", summary="门禁执行已批准版本（持久化 PlanRun + 真实 handler）")
async def execute_plan(
    plan_id: str,
    payload: PlanActionRequest,
    request: Request,
) -> dict[str, Any]:
    """Batch 5：创建持久化 PlanRun、经唯一 PlanExecutor 调用真实 handler 逐步骤执行，
    返回 run_id + 每步终态（OK/ERROR/TIMEOUT/DENIED/INVALID），不再以 PENDING 伪装完成。"""
    import hashlib

    from services.plan_audit import plan_audit_store as _audit_sink
    from services.plan_executor import PlanExecutor
    from services.tool_execution import IdempotentToolRunner

    plan = await _store().load(plan_id)
    if plan is None:
        raise _http(404, f"计划不存在: {plan_id}")

    # 批准门（执行前必须已批准且版本/主体一致）在 plan.execute 内 fail-closed
    try:
        steps = plan.execute(payload.version, payload.actor)
    except PlanGateError as exc:
        raise _http(403, str(exc))

    # 白名单工具门（fail-closed）：批准不等于越权
    import services.whitelisted_tools as _wt

    rejected = _wt.get_report_registry().validate(
        plan.required_tools(payload.version), payload.permission
    )
    if rejected:
        raise _http(403, f"计划引用了未注册或权限不足的工具: {sorted(rejected)}")

    # Batch 5：执行不得超过计划风险上限（risk_level → 权限档 fail-closed）——
    # 低风险计划不得调用写/治理类工具，即使 actor 是 author（权限更不可凌驾风险）。
    from services.plan_risk_policy import risk_to_permission

    snap = plan.snapshot(payload.version)
    cap = risk_to_permission(snap.risk_level)
    bad: list[tuple[str, str]] = []
    for s in snap.steps:
        spec = _wt.get_report_registry().spec(s.tool)
        if spec is not None and spec.required_permission > cap:
            bad.append((s.tool, spec.required_permission.name))
    if bad:
        names = ", ".join(f"{t}→{p}" for t, p in bad)
        raise _http(403, f"执行超过计划风险上限({snap.risk_level}/{cap.name})，越权工具: {names}")

    # 持久化 PlanRun 契约：run_id 由 plan+version+actor 确定性导出（幂等、可回放），
    # 每步真实 handler 结果与终态审计由 PlanExecutor + IdempotentToolRunner 落库到 audit 轨。
    runner = IdempotentToolRunner(whitelist=_wt.get_report_registry(), audit=_runner_event_to_audit(_audit_sink, plan_id, payload.version, payload.actor))
    executor = PlanExecutor(runner=runner, whitelist=_wt.get_report_registry(), audit_store=_audit_sink)

    handlers = await _resolve_handlers(request)
    results = await executor.execute_plan(
        plan, version=payload.version, actor=payload.actor,
        handlers=handlers, permission=payload.permission,
    )
    run_id = hashlib.sha256(f"{plan_id}:{payload.version}:{payload.actor}".encode()).hexdigest()[:16]
    terminal = {r.status for r in results}
    run_status = "COMPLETED" if terminal and terminal <= {"OK"} else (
        "ERROR" if any(r.status not in {"OK", "DENIED"} for r in results) else "PARTIAL"
    )
    return ok(
        {
            "plan_id": plan_id,
            "version": payload.version,
            "run_id": run_id,
            "run_status": run_status,
            "steps": [
                {
                    "tool": r.tool,
                    "run_id": r.run_id,
                    "status": r.status,
                    "idempotent": r.idempotent,
                    "message": r.message,
                    "result_available": r.result is not None,
                    "error_code": _STATUS_TO_ERROR_CODE.get(r.status, "UNKNOWN"),
                }
                for r in results
            ],
        },
        message=f"执行完成，每步均已产出终态（run_status={run_status}）",
    )


@router.get("/{plan_id}/audit", summary="读取计划执行审计轨")
async def get_plan_audit(plan_id: str) -> dict[str, Any]:
    events = await plan_audit_store.list(plan_id)
    return ok({"plan_id": plan_id, "count": len(events), "events": events}, message="审计轨已返回")


def __dict_of(plan: AgentPlan) -> dict[str, Any]:
    data = plan.to_dict()
    return {
        "plan_id": data["plan_id"],
        "workspace_id": data["workspace_id"],
        "identity": data["identity"],
        "latest_version": data["latest_version"],
        "snapshots": data["snapshots"],
    }