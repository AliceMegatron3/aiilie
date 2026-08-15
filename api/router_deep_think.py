"""
api/router_deep_think.py — 批次7：深度思考与元认知扩展系统 REST API
====================================================================
feature.deep_thinking_enable 关闭时全部路由返回 400（新增能力，不破坏旧接口）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.config_manager import config_manager
from api.deps import verify_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["DeepThink"], dependencies=[Depends(verify_token)])


def _require_feature() -> None:
    if not config_manager.get_bool("feature.deep_thinking_enable", False):
        raise HTTPException(
            status_code=400,
            detail="深度思考与元认知扩展系统未启用（feature.deep_thinking_enable=false）",
        )


def _get_service(request: Request):
    svc = getattr(request.app.state, "deep_think_service", None)
    if svc is None:
        from services.deep_think import DeepThinkService

        svc = DeepThinkService(
            task_manager=getattr(request.app.state, "task_manager", None),
            project_manager=getattr(request.app.state, "project_manager", None),
            dispatcher=getattr(request.app.state, "model_dispatcher", None),
        )
        request.app.state.deep_think_service = svc
    return svc


class DeepThinkSubmitRequest(BaseModel):
    prompt: str
    project_id: str | None = None
    doc_id: str | None = None


@router.post("/deep-think/tasks", summary="提交创作长思考任务（五阶段流水线）")
async def submit_deep_think_task(
    req: DeepThinkSubmitRequest,
    request: Request,
) -> dict[str, Any]:
    _require_feature()
    svc = _get_service(request)
    try:
        task_id = await svc.submit_task(req.prompt, req.project_id, req.doc_id)
        return {"success": True, "data": {"task_id": task_id}, "message": "长思考任务已受理", "error_code": None}
    except Exception as e:
        logger.error("长思考任务提交失败: %s", e)
        raise HTTPException(status_code=500, detail="长思考任务提交失败")


@router.get("/deep-think/reports/{task_id}", summary="获取长思考分析报告")
async def get_deep_think_report(
    task_id: str,
    request: Request,
) -> dict[str, Any]:
    _require_feature()
    svc = _get_service(request)
    report = await svc.get_report(task_id)
    if report is None:
        raise HTTPException(status_code=404, detail="分析报告尚未生成（任务进行中或不存在）")
    return {"success": True, "data": report, "message": "success", "error_code": None}


@router.get("/deep-think/checkpoints/{task_id}", summary="获取任务检查点（断点续算状态）")
async def get_deep_think_checkpoint(
    task_id: str,
    request: Request,
) -> dict[str, Any]:
    _require_feature()
    svc = _get_service(request)
    checkpoint = await svc.list_checkpoints(task_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="无检查点记录")
    return {"success": True, "data": checkpoint, "message": "success", "error_code": None}


@router.get("/deep-think/tickets", summary="获取软件进化建议书（元开发者分析）")
async def get_improvement_tickets(request: Request) -> dict[str, Any]:
    _require_feature()
    from services.deep_think import SoftwareArchitectAnalyzer

    analyzer = SoftwareArchitectAnalyzer()
    tickets = await analyzer.analyze()
    return {"success": True, "data": tickets, "message": "success", "error_code": None}


# ── 用户行为日志采集 ───────────────────────────────────────────

class BehaviorEventRequest(BaseModel):
    event_type: str  # action_path / page_stay / error_path / generic
    payload: dict[str, Any] = {}


@router.post("/behavior/events", summary="上报用户行为事件（操作路径/页面停留/报错路径）")
async def report_behavior_event(req: BehaviorEventRequest) -> dict[str, Any]:
    _require_feature()
    from core.behavior_logger import behavior_logger

    if req.event_type not in ("action_path", "page_stay", "error_path", "generic"):
        raise HTTPException(status_code=400, detail="非法事件类型")
    behavior_logger.log_event(req.event_type, req.payload)
    return {"success": True, "message": "已记录", "error_code": None}


# ── 系统架构镜像（blueprint） ──────────────────────────────────

@router.get("/system/blueprint", summary="获取系统架构镜像（architecture_map/api_spec/ui_flow）")
async def get_system_blueprint(
    request: Request,
) -> dict[str, Any]:
    _require_feature()
    from core.blueprint import blueprint_generator

    try:
        blueprints = blueprint_generator.generate_all(request.app)
    except Exception as exc:
        logger.error("架构镜像生成失败: %s", exc)
        raise HTTPException(status_code=500, detail="架构镜像生成失败")
    return {"success": True, "data": blueprints, "message": "success", "error_code": None}


@router.get("/system/blueprint/{name}", summary="按名称读取单份架构镜像")
async def get_single_blueprint(name: str) -> dict[str, Any]:
    _require_feature()
    from core.blueprint import blueprint_generator

    try:
        data = blueprint_generator.load(name)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": data, "message": "success", "error_code": None}
