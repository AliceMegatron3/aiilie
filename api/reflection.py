"""
api/reflection.py — 思考与反思系统 (Batch 4) 路由网关
===================================================
负责将后台的深度学习、统计提炼与优化执行系统暴露给前端控制台。
严格限制所有操作仅作用于 reflection 域，绝不侵入创作主业务。
"""
from __future__ import annotations
import json
import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from core.database import DatabaseManager
from models.reflection import OptimizationRule, ReflectionSession, UniversalSkill
from services.optimization_applier import OptimizationApplier
from services.reflection_trigger import ReflectionTrigger
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reflection", tags=["Reflection"], dependencies=[Depends(verify_token)])
# ==========================================
# 依赖注入桩 (将在 main.py 统一挂载至 app.state)
# ==========================================
def get_reflection_trigger(request: Request) -> ReflectionTrigger:
    return request.app.state.reflection_trigger
def get_optimization_applier(request: Request) -> OptimizationApplier:
    return request.app.state.optimization_applier
def get_db_manager(request: Request) -> DatabaseManager:
    return request.app.state.db
# ==========================================
# Request Models
# ==========================================
class TriggerRequest(BaseModel):
    priority: int = 50
    scope_filter: str = "ALL"
class EventReportRequest(BaseModel):
    event_type: str
    details: dict[str, Any] = {}
class ToggleRuleRequest(BaseModel):
    is_active: bool
# ==========================================
# API: 任务触发与监控
# ==========================================
@router.post("/task/trigger", summary="手动发起系统反思采集任务")
async def trigger_reflection(
    payload: TriggerRequest,
    trigger: ReflectionTrigger = Depends(get_reflection_trigger)
) -> dict[str, str]:
    """触发长耗时的跨库数据审计与挖掘分析任务。"""
    try:
        session_id = await trigger.trigger(trigger_type="MANUAL")
        return {"message": "反思任务已成功投递至后台队列", "session_id": session_id}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error("反思任务投递失败: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")
@router.post("/task/event/report", summary="系统事件上报触发自动反思")
async def report_event_trigger(
    payload: EventReportRequest,
    trigger: ReflectionTrigger = Depends(get_reflection_trigger)
) -> dict[str, str]:
    """系统级事件接收入口。当发生连续异常或满足某规则时自动投递低优先级AUTO反思。"""
    try:
        session_id = await trigger.trigger(trigger_type="AUTO")
        return {"message": f"事件 {payload.event_type} 已受理，已触发后台自动反思", "session_id": session_id}
    except RuntimeError as e:
        # 如果正在进行反思，返回忽略提示而不是报错
        return {"message": "当前已有反思任务在进行中，本次事件触发已忽略。"}
    except Exception as e:
        logger.error("事件触发反思失败: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")
@router.get("/task/{task_id}/progress", summary="查询反思任务进度")
async def get_task_progress(
    task_id: str,
    db: DatabaseManager = Depends(get_db_manager)
) -> dict[str, Any]:
    """读取批次1通用任务表，获取后台反思任务是否已完成。"""
    try:
        # 查询任务表
        cursor = await db.conn.execute("SELECT status, retry_count FROM tasks WHERE task_id = ?", (task_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="未找到对应的反思任务记录")
            
        status, retry_count = row
        return {
            "task_id": task_id,
            "status": status,
            "retry_count": retry_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查询任务进度失败: %s", e)
        raise HTTPException(status_code=500, detail="数据库检索异常")
# ==========================================
# API: 报告与会话查询
# ==========================================
@router.get("/report/list", response_model=list[ReflectionSession], summary="分页查询历史反思会话")
async def list_reflection_sessions(
    limit: int = 50,
    offset: int = 0,
    db: DatabaseManager = Depends(get_db_manager)
) -> list[ReflectionSession]:
    """获取之前所有的反思与自我学习历史记录 (按时间倒序)。"""
    try:
        cursor = await db.conn.execute(
            "SELECT * FROM reflection_sessions ORDER BY start_time DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        
        results = []
        for row in rows:
            results.append(ReflectionSession(**dict(zip(columns, row))))
        return results
    except Exception as e:
        logger.error("读取会话列表失败: %s", e)
        raise HTTPException(status_code=500, detail="数据库检索异常")
# ==========================================
# API: 优化规则治理
# ==========================================
@router.get("/rule/active", response_model=list[OptimizationRule], summary="查询已激活的优化规则")
async def list_active_rules(
    db: DatabaseManager = Depends(get_db_manager)
) -> list[OptimizationRule]:
    """拉取当前影响着批次1/2/3运行底层的全部动态生效规则 (is_active = 1)。"""
    try:
        cursor = await db.conn.execute("SELECT * FROM optimization_rules WHERE is_active = 1 ORDER BY confidence DESC")
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        
        results = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            # 安全解析 JSON 字段，处理 NULL 或非法 JSON
            row_dict["condition"] = _safe_json_loads(row_dict.get("condition"))
            row_dict["action"] = _safe_json_loads(row_dict.get("action"))
            row_dict["is_active"] = bool(row_dict["is_active"])
            results.append(OptimizationRule(**row_dict))
        return results
    except Exception as e:
        logger.error("读取激活规则列表失败: %s", e)
        raise HTTPException(status_code=500, detail="数据库检索异常")
@router.put("/rule/{rule_id}/toggle", summary="一键启用/禁用系统规则")
async def toggle_rule(
    rule_id: str,
    payload: ToggleRuleRequest,
    applier: OptimizationApplier = Depends(get_optimization_applier)
) -> dict[str, str]:
    """对提取器挖掘出的异常规则进行手动强制熔断或恢复。"""
    try:
        await applier.toggle_rule_status(rule_id, payload.is_active)
        status_str = "启用" if payload.is_active else "禁用"
        return {"message": f"规则 {rule_id} 已被人工强制{status_str}。"}
    except Exception as e:
        logger.error("切换规则状态失败: %s", e)
        raise HTTPException(status_code=500, detail="状态更新失败")
# ==========================================
# API: 技能沉淀资产
# ==========================================
@router.get("/skill/list", response_model=list[UniversalSkill], summary="查看通用技能资产")
async def list_universal_skills(
    db: DatabaseManager = Depends(get_db_manager)
) -> list[UniversalSkill]:
    """展示从高频卡片与优质小说风格中成功提炼并沉淀下来的大模型 Prompt 模板。"""
    try:
        cursor = await db.conn.execute("SELECT * FROM universal_skills ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        
        results = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            row_dict["content"] = _safe_json_loads(row_dict.get("content"))
            row_dict["source_cards"] = _safe_json_loads(row_dict.get("source_cards"))
            results.append(UniversalSkill(**row_dict))
        return results
    except Exception as e:
        logger.error("读取通用技能列表失败: %s", e)
        raise HTTPException(status_code=500, detail="数据库检索异常")
def _safe_json_loads(value: Any, default: Any = None) -> Any:
    """安全地解析 JSON 字符串，处理 NULL、空字符串和非法 JSON。"""
    if value is None:
        return default if default is not None else {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}