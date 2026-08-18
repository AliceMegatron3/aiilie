"""受限编程副驾驶 API。

第一版只提供计划、作者批准后的 L2 验证执行和结果查询；不提供 shell、PowerShell、依赖安装或正式工作区写入。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_code_execution_service, get_db, get_task_manager, verify_token
from core.config_manager import config_manager
from core.response import ok
from models.code_execution import CodePlan, CodeTaskRequest
from models.task import CodeExecutionTask
from services.code_execution import CodeExecutionService, CodePolicyError

router = APIRouter(prefix="/code", tags=["Code Execution"], dependencies=[Depends(verify_token)])
code_service = None


def _code_execution_enabled() -> bool:
    """受限代码执行总开关（阶段A fail-closed；隔离 worker 建成前默认关闭）。"""
    return bool(config_manager.get_bool("code_execution.enabled", False))


def _disabled_response(message: str) -> dict[str, Any]:
    raise HTTPException(
        status_code=501,
        detail=message,
        headers={"X-Feature-Status": "disabled"},
    )


class CodeRunRequest(BaseModel):
    request: CodeTaskRequest
    plan_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=256)


class CodeDiffRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=2 * 1024 * 1024)


class CodeStageRequest(CodeDiffRequest):
    base_digest: str | None = None


class CodeApprovalRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=256)


@router.get("/snapshot")
async def code_snapshot(target: str = ".", code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    try:
        return ok(code_service.snapshot(target))
    except CodePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/stage")
async def stage_code_file(payload: CodeStageRequest, code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    try:
        return ok(code_service.stage_file(payload.path, payload.content, payload.base_digest), message="变更已进入 staging，尚未写入正式工作区")
    except CodePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/promote/{change_id}")
async def promote_code_change(change_id: str, payload: CodeApprovalRequest, code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    if not _code_execution_enabled():
        _disabled_response("受限代码执行已禁用（code_execution.enabled=false），正式写入/审批暂不可用")
    try:
        return ok(code_service.promote(change_id, payload.approval_id), message="变更已批准并应用")
    except CodePolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rollback/{change_id}")
async def rollback_code_change(change_id: str, payload: CodeApprovalRequest, code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    if not _code_execution_enabled():
        _disabled_response("受限代码执行已禁用（code_execution.enabled=false），正式回滚暂不可用")
    try:
        return ok(code_service.rollback(change_id, payload.approval_id), message="变更已回滚")
    except CodePolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/diff")
async def code_diff(payload: CodeDiffRequest, code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    try:
        return ok(code_service.diff(payload.path, payload.content))
    except CodePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/plan")
async def create_code_plan(payload: CodeTaskRequest, code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    try:
        plan = code_service.plan(payload)
        return ok(plan.model_dump(mode="json"), message="代码计划已生成，等待作者批准")
    except CodePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/run")
async def run_code(payload: CodeRunRequest, task_manager=Depends(get_task_manager), code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    # 阶段A fail-closed：审批闭环与隔离 worker 未就绪前，拒绝一切代码执行。
    if not _code_execution_enabled():
        _disabled_response("受限代码执行已禁用（code_execution.enabled=false），拒绝在宿主环境执行代码")
    try:
        plan = code_service.plans.get(payload.plan_id)
        if plan is None:
            raise CodePolicyError("代码计划不存在或已失效")
        if plan.workspace_id != payload.request.workspace_id:
            raise CodePolicyError("计划与执行请求的 workspace_id 不一致")
        if plan.files_to_read != [payload.request.target] or (
            payload.request.resource_profile != "auto"
            and plan.resource_profile != payload.request.resource_profile
        ):
            raise CodePolicyError("执行请求与服务端签发计划不一致")
        # 审批凭证必须是服务端签发的有效一次性凭证（阶段B：拒绝客户端伪造 approval_id）。
        approved = code_service.verify_approval(plan, payload.approval_id)
        task = CodeExecutionTask(
            priority=4,
            status="PENDING",
            raw_command=f"code {payload.request.command} {payload.request.target}",
            request_payload=payload.request.model_dump(mode="json"),
            plan_payload={**approved.model_dump(mode="json"), "approval_id": payload.approval_id},
        )
        persisted = await task_manager.submit_task(task)
        return ok({"task_id": persisted.task_id, "plan_id": approved.plan_id, "status": "PENDING"}, message="代码任务已进入持久化队列")
    except CodePolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/events")
async def get_code_task_events(task_id: str, after_sequence: int = 0) -> dict[str, Any]:
    from api.websocket import manager
    return ok(manager.task_history(task_id, after_sequence=after_sequence))


@router.get("/tasks/{task_id}/state")
async def get_code_task_state(task_id: str, db=Depends(get_db), code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    state = code_service.get_task_record(task_id)
    if state is None:
        row = await db.get_task(task_id)
        if row and row.get("task_type") == "code_execution":
            try:
                payload = json.loads(row.get("task_payload") or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            state = payload.get("code_execution", {}).get("record")
            if state is None:
                state = {
                    "task_id": task_id,
                    "status": row.get("status"),
                    "phase": "PLANNING",
                    "progress": 0,
                    "message": "任务已持久化",
                }
    if state is None:
        raise HTTPException(status_code=404, detail="代码任务不存在")
    return ok(state)


@router.post("/tasks/{task_id}/cancel")
async def cancel_code_task(
    task_id: str,
    db=Depends(get_db),
    task_manager=Depends(get_task_manager),
    code_service=Depends(get_code_execution_service),
) -> dict[str, Any]:
    row = await db.get_task(task_id)
    if row is None or row.get("task_type") != "code_execution":
        raise HTTPException(status_code=404, detail="代码任务不存在")
    status = str(row.get("status", ""))
    if status == "PENDING":
        if not await task_manager.cancel_task(task_id):
            raise HTTPException(status_code=409, detail="代码任务无法取消")
        return ok({"task_id": task_id, "status": "CANCELLED", "phase": "PLANNING"}, message="代码任务已取消")
    if status == "RUNNING":
        try:
            state = await code_service.cancel(task_id)
        except CodePolicyError as exc:
            raise HTTPException(status_code=409, detail="代码任务正在启动，暂不可取消") from exc
        return ok(state, message="代码任务已请求取消")
    return ok({"task_id": task_id, "status": status}, message="代码任务已处于终态")


@router.get("/tasks/{task_id}")
async def get_code_task(task_id: str, db=Depends(get_db), code_service=Depends(get_code_execution_service)) -> dict[str, Any]:
    result = code_service.get_result(task_id)
    if result is not None:
        return ok(result)
    row = await db.get_task(task_id)
    if row is None or row.get("task_type") != "code_execution":
        raise HTTPException(status_code=404, detail="代码任务不存在")
    try:
        payload = json.loads(row.get("task_payload") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    persisted = payload.get("code_execution", {}).get("result")
    if persisted is None:
        raise HTTPException(status_code=409, detail="代码任务尚未生成结果")
    return ok(persisted)
