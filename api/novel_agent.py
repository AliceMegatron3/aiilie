"""
api/novel_agent.py — 多智能体小说创作自学习闭环 API (第十部分)
==============================================================
新增接口：
  POST /api/v1/novel-agent/{task_id}/feedback           用户对生成结果反馈打分、标记OOC
  GET  /api/v1/novel-agent/skills                       获取沉淀的智能体技能列表（含调用统计与effect_score）
  POST /api/v1/novel-agent/skills/{skill_id}/toggle     手动启用/归档指定技能
受 feature.novel_multi_agent_enable 总开关门控（由 api_router 挂载时控制）。
"""
from __future__ import annotations
import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/novel-agent", tags=["Novel Agent"], dependencies=[Depends(verify_token)])
# ==========================================
# 依赖注入桩
# ==========================================
def get_audit_store(request: Request):
    return getattr(request.app.state, "novel_agent_audit_store", None)
def get_skill_store(request: Request):
    return getattr(request.app.state, "novel_agent_skill_store", None)
def get_learning_loop(request: Request):
    return getattr(request.app.state, "novel_agent_learning_loop", None)
# ==========================================
# Request Models
# ==========================================
class FeedbackRequest(BaseModel):
    """用户反馈请求体。"""
    score: float | None = Field(default=None, ge=0.0, le=5.0, description="打分 0~5")
    ooc_marked: bool = Field(default=False, description="是否标记 OOC")
    comment: str = Field(default="", description="补充说明")
class ToggleSkillRequest(BaseModel):
    """技能状态切换请求体。"""
    status: str = Field(..., pattern="^(ACTIVE|ARCHIVED|PENDING_REVIEW)$", description="目标状态")
# ==========================================
# API: 用户反馈
# ==========================================
@router.post("/{task_id}/feedback", summary="用户对生成结果反馈打分、标记OOC")
async def submit_feedback(
    task_id: str,
    payload: FeedbackRequest,
    audit_store=Depends(get_audit_store),
) -> dict[str, Any]:
    """写入审计数据，作为反思样本。"""
    if audit_store is None:
        raise HTTPException(status_code=503, detail="多智能体审计服务未装配")
    try:
        ok = await audit_store.update_feedback(
            task_id=task_id,
            score=payload.score,
            ooc_marked=payload.ooc_marked,
            comment=payload.comment,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="未找到该任务的审计记录")
        return {
            "success": True,
            "message": "反馈已记录，将作为反思样本",
            "task_id": task_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("提交反馈失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="反馈提交失败")
# ==========================================
# API: 技能列表
# ==========================================
@router.get("/skills", summary="获取沉淀的智能体技能列表（含调用统计与effect_score）")
async def list_skills(
    limit: int = 50,
    offset: int = 0,
    skill_store=Depends(get_skill_store),
) -> dict[str, Any]:
    if skill_store is None:
        raise HTTPException(status_code=503, detail="多智能体技能服务未装配")
    try:
        skills = await skill_store.list_skills(limit=limit, offset=offset)
        return {"success": True, "total": len(skills), "skills": skills}
    except Exception as e:
        logger.error("技能列表查询失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="技能列表查询失败")
# ==========================================
# API: 技能启停/归档
# ==========================================
@router.post("/skills/{skill_id}/toggle", summary="手动启用/归档指定技能")
async def toggle_skill(
    skill_id: str,
    payload: ToggleSkillRequest,
    skill_store=Depends(get_skill_store),
) -> dict[str, Any]:
    if skill_store is None:
        raise HTTPException(status_code=503, detail="多智能体技能服务未装配")
    try:
        ok = await skill_store.toggle_skill(skill_id, payload.status)
        if not ok:
            raise HTTPException(status_code=404, detail="技能不存在")
        return {
            "success": True,
            "message": f"技能 {skill_id} 状态已切换为 {payload.status}",
            "skill_id": skill_id,
            "status": payload.status,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("技能状态切换失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="技能状态切换失败")