from fastapi import APIRouter, Depends, HTTPException
import logging
import uuid
from datetime import datetime, timezone
from services.sync_provider import LocalSyncProvider
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_token)])
sync_provider = LocalSyncProvider()
# 记录每个项目最近一次同步时间(内存态;SyncProvider 不持久化真实状态)
_sync_last_time: dict[str, str] = {}


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
        _sync_last_time[project_id] = datetime.now(timezone.utc).isoformat()
        return {"status": "success", "message": "Sync task queued in background.", "task_id": sync_task_id}
    except Exception as e:
        logger.error(f"同步任务提交失败: {e}")
        raise HTTPException(status_code=500, detail="同步任务提交失败")


@router.get("/projects/{project_id}/sync-status")
async def get_sync_status(project_id: str):
    """补丁D扩展：获取同步状态

    批次1修复:不再返回硬编码 idle——最近一次成功 push 后返回 last_sync_time。
    (SyncProvider 本身不持久化,故为内存态;应用重启后归零为 idle。)
    """
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id 不能为空")
    last_time = _sync_last_time.get(project_id)
    return {"status": "success", "data": {
        "status": "synced" if last_time else "idle",
        "last_sync_time": last_time,
        "project_id": project_id,
    }}