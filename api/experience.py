from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import logging

from services.experience_manager import experience_manager
from api.deps import verify_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/experience", tags=["Experience & Reflection"], dependencies=[Depends(verify_token)])

class ExperienceCreate(BaseModel):
    exp_type: str
    content: str

class ExperienceApprove(BaseModel):
    is_approved: bool

@router.post("/", summary="[Agent Tool] 智能体提交新经验")
async def submit_experience(
    payload: ExperienceCreate
) -> dict[str, Any]:
    """智能体通过此接口提交它刚刚总结的经验，将进入 pending 状态等待审批。"""
    try:
        exp = experience_manager.add_experience(payload.exp_type, payload.content)
        return {"success": True, "data": exp, "message": "经验已提交待审，并同步至本地报告"}
    except Exception as e:
        logger.error("提交经验失败: %s", e)
        raise HTTPException(status_code=500, detail="提交经验失败")

@router.get("/", summary="获取经验列表")
async def list_experiences(
    status: str = None
) -> dict[str, Any]:
    """前端或智能体查询经验库（可按状态过滤，如 approved 或 pending）。"""
    try:
        data = experience_manager.get_experiences(status)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail="获取经验失败")

@router.put("/{exp_id}/approval", summary="人类审批经验")
async def approve_experience(
    exp_id: str,
    payload: ExperienceApprove
) -> dict[str, Any]:
    """主人同意或拒绝智能体的经验总结。"""
    try:
        success = experience_manager.approve_experience(exp_id, payload.is_approved)
        if not success:
            raise HTTPException(status_code=404, detail="经验ID不存在")
        return {"success": True, "message": "审批完成，本地报告已更新"}
    except Exception as e:
        logger.error("审批经验失败: %s", e)
        raise HTTPException(status_code=500, detail="审批失败")

@router.get("/quantize-prompt", summary="[Agent Tool] 动态拉取量化经验")
async def get_quantize_prompt() -> dict[str, Any]:
    """
    量化智能体在初始化 Prompt 时调用此接口。
    将返回所有 `status=approved` 且与量化相关的经验，供其“考前复习”。
    """
    try:
        data = experience_manager.get_experiences(status="approved")
        # 过滤出量化、通用和模型认知的经验
        relevant_exps = [
            e["content"] for e in data 
            if e["exp_type"] in ("quantize", "workflow", "creative", "model_insight")
        ]
        
        prompt_injection = "【历史人类批准的经验法则】\n"
        if not relevant_exps:
            prompt_injection += "暂无历史经验。"
        else:
            for idx, exp in enumerate(relevant_exps, 1):
                prompt_injection += f"{idx}. {exp}\n"
                
        return {"success": True, "prompt_injection": prompt_injection}
    except Exception as e:
        raise HTTPException(status_code=500, detail="获取经验 prompt 失败")
