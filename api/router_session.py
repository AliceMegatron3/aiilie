from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from services.session_pool import session_pool
from api.deps import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])

class CreateSessionReq(BaseModel):
    model_key: str
    bind_project_id: Optional[str] = None
    bind_doc_id: Optional[str] = None
    bind_branch_id: Optional[str] = None
    # 边界校验：防异常值导致内存/磁盘膨胀
    ttl: int = Field(default=43200, ge=60, le=30 * 24 * 3600, description="会话有效期（秒），1分钟~30天")
    max_msgs: int = Field(default=25, ge=1, le=10000, description="最大消息轮数")

@router.post("/sessions/create")
async def create_session(req: CreateSessionReq):
    """补丁F扩展：创建独立状态的多模型会话"""
    session_id = session_pool.create_session(
        model_key=req.model_key,
        bind_project=req.bind_project_id,
        bind_doc=req.bind_doc_id,
        bind_branch=req.bind_branch_id,
        ttl=req.ttl,
        max_msgs=req.max_msgs
    )
    if not session_id:
        raise HTTPException(status_code=503, detail="Session pool is disabled.")
    return {"status": "success", "session_id": session_id}

@router.get("/sessions/list")
async def get_sessions_list():
    sessions = session_pool.get_all_sessions()
    return {"status": "success", "data": sessions}

@router.get("/sessions/stats")
async def get_sessions_stats():
    stats = session_pool.get_stats()
    return {"status": "success", "data": stats}

@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = session_pool.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return {"status": "success", "data": session.model_dump()}

@router.post("/sessions/{session_id}/freeze")
async def freeze_session(session_id: str):
    session_pool.freeze_session(session_id)
    return {"status": "success", "message": "Session frozen."}

@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    session_pool.destroy_session(session_id)
    return {"status": "success"}
