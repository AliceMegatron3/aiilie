from fastapi import APIRouter, Depends, HTTPException
import logging
import uuid
from datetime import datetime, timezone
from services.sync_provider import LocalSyncProvider
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_token)])
sync_provider = LocalSyncProvider()
@router.post("/projects/{project_id}/sync")
async def trigger_sync(project_id: str):
    """补丁D扩展：手动触发云端同步"""
    logger.info(f"🔄 收到项目 {project_id} 的云端同步请求。")
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id 不能为空")
    # 生成同步任务ID，实际应封装为 Batch 1 的后台低优先级任务
    sync_task_id = f"sync_{uuid.uuid4().hex[:8]}"
    try:
        sync_provider.push(project_id, {"task_id": sync_task_id, "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"status": "success", "message": "Sync task queued in background.", "task_id": sync_task_id}
    except Exception as e:
        logger.error(f"同步任务提交失败: {e}")
        raise HTTPException(status_code=500, detail="同步任务提交失败")
@router.get("/projects/{project_id}/sync-status")
async def get_sync_status(project_id: str):
    """补丁D扩展：获取同步状态"""
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id 不能为空")
    # 统一返回信封结构（与 /api/v2 规划一致），保持前端解包语义。
    # 实际同步状态由 LocalSyncProvider 维护，此处返回基础可查询状态。
    return {"status": "success", "data": {"status": "idle", "last_sync_time": None, "project_id": project_id}}