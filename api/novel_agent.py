"""
api/novel_agent.py — 多智能体小说创作自学习闭环 API (第十部分)
==============================================================
新增接口：
  POST /api/v1/novel-agent/{task_id}/feedback           用户对生成结果反馈打分、标记OOC
  GET  /api/v1/novel-agent/skills                       获取沉淀的智能体技能列表（含调用统计与effect_score）
  POST /api/v1/novel-agent/skills/{skill_id}/toggle     手动启用/归档指定技能
受 feature.novel_multi_agent_enable 总开关门控。阶段C 起本路由始终挂载，
在请求时由 _feature_gate 运行时裁决（关闭时返回 400），实现无重启热切换。
"""
from __future__ import annotations
import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from core.config_manager import config_manager
from api.deps import get_skill_governance, verify_token
logger = logging.getLogger(__name__)


def _feature_gate() -> None:
    """nover_agent 功能开关运行时裁决（与 timeline/tts/storyboard 一致）。"""
    if not config_manager.get_bool("feature.novel_multi_agent_enable", False):
        raise HTTPException(
            status_code=400,
            detail="多智能体小说自学习闭环未启用（feature.novel_multi_agent_enable=false）",
        )


router = APIRouter(
    prefix="/novel-agent",
    tags=["Novel Agent"],
    dependencies=[Depends(verify_token), Depends(_feature_gate)],
)
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
    governance=Depends(get_skill_governance),
) -> dict[str, Any]:
    try:
        candidates = governance.list_candidates()
        skills = []
        for candidate in candidates[offset:offset + min(max(limit, 1), 200)]:
            version = governance.get_active_version(candidate["candidate_id"])
            artifact = (version or {}).get("snapshot", {}).get("artifact", {})
            if "novel_agent_skill" not in artifact:
                continue
            skills.append({
                "skill_id": candidate["candidate_id"],
                "name": candidate["name"],
                "status": candidate["status"],
                "payload": artifact["novel_agent_skill"],
                "governance_version": (version or {}).get("version"),
            })
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
    governance=Depends(get_skill_governance),
) -> dict[str, Any]:
    candidate = next((item for item in governance.list_candidates() if item["candidate_id"] == skill_id), None)
    if candidate is None:
        raise HTTPException(status_code=404, detail="治理技能不存在")
    if payload.status == "ACTIVE":
        raise HTTPException(status_code=409, detail="ACTIVE 发布必须经治理全量发布接口")
    if payload.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="ARCHIVED 请使用治理停止灰度或版本回滚接口")
    return {"success": True, "message": "技能仍处于治理候选流程", "skill_id": skill_id, "status": candidate["status"]}