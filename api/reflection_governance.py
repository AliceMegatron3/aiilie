from fastapi import APIRouter, Depends, HTTPException
import logging
import uuid
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_token)])
@router.get("/reflection/pending-rules")
async def get_pending_rules():
    """补丁B扩展：人工审核界面，获取待审核规则"""
    # 实际应从数据库查询 pending 状态的规则
    # 此处返回空列表，由前端展示空状态
    return {"data": []}
@router.post("/reflection/rules/{rule_id}/approve")
async def approve_rule(rule_id: str):
    """补丁B扩展：管理员手动批准一条高置信度规则上线"""
    if not rule_id:
        raise HTTPException(status_code=400, detail="rule_id 不能为空")
    logger.info(f"✅ 人工干预：规则 {rule_id} 已被批准上线！")
    # 实际应更新数据库中规则的 is_active 状态
    return {"status": "success", "message": f"Rule {rule_id} activated.", "rule_id": rule_id}