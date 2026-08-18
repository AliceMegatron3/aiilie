from fastapi import APIRouter, Depends, HTTPException
import logging
import uuid
from datetime import datetime, timezone
from core.feature_status import feature_disabled
from services.sync_provider import LocalSyncProvider
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_token)])
sync_provider = LocalSyncProvider()
# 记录每个项目最近一次同步时间(内存态;SyncProvider 不持久化真实状态)
_sync_last_time: dict[str, str] = {}


@router.post("/projects/{project_id}/sync")
async def trigger_sync(project_id: str):
    """补丁D扩展：手动触发云端同步

    阶段C：真实云端 provider 未接入（当前仅 LocalSyncProvider 内存态），
    按方向报告 B 类降级——返回结构化 DISABLED，不伪装“已同步成功”。
    """
    logger.info(f"🔄 收到项目 {project_id} 的云端同步请求。")
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id 不能为空")
    return feature_disabled("project.sync", "provider_not_configured")


@router.get("/projects/{project_id}/sync-status")
async def get_sync_status(project_id: str):
    """补丁D扩展：获取同步状态（真实云端未接入，标记 DISABLED）"""
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id 不能为空")
    return feature_disabled("project.sync", "provider_not_configured")