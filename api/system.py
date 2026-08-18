"""
api/system.py — 批次5全局中枢 API 路由
===================================================
向前端暴露统一的自然语言/结构化下发入口，
以及全系统状态查询大屏所需的核心指标接口。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from pydantic import BaseModel
from models.system import CommandRequest, SystemState
from core.state_manager import StateManager
from core.response import fail, ok
from services.priority_queue import PriorityTaskQueue
from services.system_monitor import SystemMonitor
from services.global_router import GlobalRouter
from services.docling_guard import check_docling_readiness
from api.deps import _state, get_db, verify_token

logger = logging.getLogger(__name__)

# 为避免与其它业务路由冲突，前缀设置为 /api/v1 (在 main.py 可能被统一包一层)，这里简化为根目录内自身定义
router = APIRouter(prefix="/system", tags=["System Control Center"], dependencies=[Depends(verify_token)])


# ==========================================
# 依赖注入桩
# ==========================================
def get_state_manager(request: Request) -> StateManager:
    return _state(request, "state_manager")

def get_task_queue(request: Request) -> PriorityTaskQueue:
    return _state(request, "priority_task_queue")

def get_system_monitor(request: Request) -> SystemMonitor:
    return _state(request, "system_monitor")

def get_global_router(request: Request) -> GlobalRouter:
    return _state(request, "global_router")


# ==========================================
# 接口定义
# ==========================================
@router.post("/command", summary="全局统一入口：接收前端指令并智能路由")
async def execute_command(
    req: CommandRequest,
    global_router: GlobalRouter = Depends(get_global_router)
) -> dict[str, Any]:
    """
    接收用户任意形式的输入，经过 Batch 5 全局路由器意图识别、
    策略挂载、负载判定后，投递给具体的底层批次管线执行。
    """
    try:
        result = await global_router.route_command(req)
        return result
    except ValueError as e:
        logger.warning("[API] 请求被拦截 (校验失败): %s", e)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("[API] 统一指令网关异常崩溃: %s", e)
        raise HTTPException(status_code=500, detail="中枢系统路由失败，请查看后台日志。")


@router.get("/command/{task_id}/result", summary="按任务ID拉取创作结果(兜底查询)")
async def get_command_result(
    task_id: str,
    db=Depends(get_db),
) -> dict[str, Any]:
    """结果兜底查询(批次1)。

    异步创作经 WebSocket 推送结果;本端点解耦推送——作者端重连后
    可主动拉取,避免\"返回 queued 但查不到结果\"的体验断裂。
    """
    record = await db.get_chat_history_by_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="该任务尚无对话记录(可能仍在执行或未落库)")
    result = record.get("ai_result", "")
    empty = not (result or "").strip()
    if empty:
        logger.warning("[System] 兜底查询发现空产出 task=%s", task_id)
    return ok(
        {
            "task_id": task_id,
            "session_id": record.get("session_id"),
            "project_id": record.get("project_id"),
            "user_query": record.get("user_query"),
            "ai_result": result,
            "empty_result": empty,
            "timestamp": record.get("timestamp"),
        },
        message="success",
    )


@router.get("/status", summary="监控大盘：拉取系统级健康探针及状态")
async def get_system_status(
    state_manager: StateManager = Depends(get_state_manager),
    system_monitor: SystemMonitor = Depends(get_system_monitor)
) -> dict[str, Any]:
    """
    整合 psutil 物理机指标、内部 SQLite IO 健康度，以及 StateManager 的原子运行态。
    """
    current_state = await state_manager.get_state()
    health_report = system_monitor.get_health_report()
    
    return ok(
        {
            "global_state": current_state.value,
            "health_report": health_report,
        },
        message="success",
    )


@router.get("/queue", summary="队列大屏：查看缓冲任务漏斗的排队情况")
async def get_system_queue(
    request: Request,
    task_queue: PriorityTaskQueue = Depends(get_task_queue)
) -> dict[str, Any]:
    """
    查看所有挂起等待批次1消化的大体积排队任务状态，
    以及批次1分段执行引擎（services.task_manager.TaskManager）的实时负载。
    """
    batch1 = getattr(request.app.state, "batch1_task_manager", None)
    if batch1 is not None:
        batch1_info = {
            "mounted": True,
            "queue_size": batch1.queue_size,
            "tracked_tasks": batch1.total_tasks,
        }
    else:
        batch1_info = {"mounted": False}

    return ok(
        {
            "pending_tasks_count": task_queue.qsize(),
            "active_task_id": task_queue.current_task_id,
            "batch1_engine": batch1_info,
        },
        message="success",
    )


@router.get("/docling/readiness", summary="Docling 可选解析器生产 readiness 检查")
async def get_docling_readiness() -> dict[str, Any]:
    """
    只读端点：返回 docling 可选解析器在生产环境的就绪状态。
    字段：enabled（开关）/ installed（是否安装）/ offline_ready（离线模型工件是否就绪）/
    limits（页数/CPU线程/超时/并发上限）/ errors（未就绪原因列表）。
    完整路径：GET /api/v1/system/docling/readiness
    """
    return ok(check_docling_readiness(), message="docling readiness 检查完成")


@router.post("/interrupt", summary="紧急核爆开关：无视状态强制中断系统")
async def interrupt_system(
    state_manager: StateManager = Depends(get_state_manager)
) -> dict[str, str]:
    """
    前端点下“停止”时调用，将系统状态强行掰回 IDLE。
    批次1等底层轮询队列看到系统状态不是 RUNNING_* 即会自动丢弃后续处理并中断。
    """
    await state_manager.set_state(SystemState.IDLE)
    logger.warning("[API] 用户人工按下中断红钮，系统独占锁被强制释放回 IDLE，正在强行阻断下游管线！")
    return ok(None, message="全局中断信号已广播，系统状态强制复位空闲。")

class ContextLockRequest(BaseModel):
    project_id: str | None

@router.post("/context-lock", summary="设置智能体上下文锁定")
async def lock_context(
    req: ContextLockRequest,
    state_manager: StateManager = Depends(get_state_manager)
) -> dict[str, str]:
    """
    当作者在前端进入某个子项目时，锁定全局智能体的作用域，
    确保所有回答和索引都局限于当前子项目，防止幻觉串车。
    传入 null (None) 即可解除锁定回到全局模式。
    """
    await state_manager.set_context_lock(req.project_id)
    if req.project_id:
        return ok(None, message=f"上下文已安全锁定至子项目: {req.project_id}")
    return ok(None, message="上下文锁定已解除，恢复全局可见性。")


# ==========================================
# 补丁E剩余业务：资源统计 / 垃圾回收 / GC 报告
# ==========================================

# 目录占用统计缓存（避免每次请求全量遍历）
_dir_usage_cache: dict[str, Any] = {"data": {}, "ts": 0.0}
_DIR_CACHE_TTL = 30.0


def _dir_size_mb(path: Any) -> float:
    """统计目录体积（MB）。"""
    p = Path(path)
    if not p.exists():
        return 0.0
    try:
        total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return round(total / (1024 ** 2), 2)
    except OSError:
        return 0.0


async def _collect_dir_usage() -> dict[str, float]:
    """统计 temp / library / projects 目录占用（带缓存，to_thread 防阻塞）。"""
    global _dir_usage_cache
    import time

    now = time.monotonic()
    if _dir_usage_cache["data"] and now - _dir_usage_cache["ts"] < _DIR_CACHE_TTL:
        return _dir_usage_cache["data"]

    from core.path_resolver import get_temp_root, get_app_data_dir, get_workspace_dir

    temp_dir = str(get_temp_root())
    library_dir = str(get_app_data_dir().parent / "library")
    projects_dir = str(get_workspace_dir())

    temp_mb, lib_mb, proj_mb = await asyncio.to_thread(
        lambda: (_dir_size_mb(temp_dir), _dir_size_mb(library_dir), _dir_size_mb(projects_dir))
    )
    data = {"temp": temp_mb, "library": lib_mb, "projects": proj_mb}
    _dir_usage_cache = {"data": data, "ts": now}
    return data


@router.get("/resource-stats", summary="存储资源统计：目录占用与磁盘剩余")
async def get_resource_stats(request: Request) -> dict[str, Any]:
    """返回临时文件/书库/项目目录占用与磁盘剩余空间。"""
    import psutil

    dir_usage = await _collect_dir_usage()
    disk = psutil.disk_usage(str(Path.home().anchor))
    return ok(
        {
            "directory_usage_mb": dir_usage,
            "disk_free_gb": round(disk.free / (1024 ** 3), 2),
            "disk_total_gb": round(disk.total / (1024 ** 3), 2),
            "disk_percent": disk.percent,
        }
    )


@router.post("/gc-run", summary="手动触发垃圾回收（临时清理 + 空闲 VACUUM）")
async def trigger_gc(request: Request) -> dict[str, Any]:
    """提交低优先级 GC 后台任务，返回 task_id。"""
    gc_manager = getattr(request.app.state, "gc_task_manager", None)
    if gc_manager is None:
        raise HTTPException(status_code=503, detail="GC 任务管理器未初始化")
    task_id = gc_manager.submit_gc()
    return ok({"task_id": task_id}, message="GC 任务已受理（VACUUM 仅在系统空闲时执行）")


@router.get("/gc/{task_id}/report", summary="查询 GC 任务执行报告")
async def get_gc_report(task_id: str, request: Request) -> dict[str, Any]:
    """返回 GC 任务状态与清理/压缩结果。"""
    gc_manager = getattr(request.app.state, "gc_task_manager", None)
    if gc_manager is None:
        raise HTTPException(status_code=503, detail="GC 任务管理器未初始化")
    report = gc_manager.get_report(task_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"GC 任务不存在: {task_id}")
    return ok(report)

@router.get("/sessions/{session_id}/chat-history", summary="获取会话的完整对话历史")
async def get_chat_history(
    session_id: str,
    request: Request
) -> dict[str, Any]:
    """
    读取指定会话的完整历史记录供前端对话面板加载。
    """
    try:
        from core.database import DatabaseManager
        # 优先使用 app.state.db（全局数据库实例），避免依赖 GlobalRouter 内部结构
        db = getattr(request.app.state, "db", None)
        if db is None:
            # 回退：尝试从 GlobalRouter 的 project_manager 获取
            global_router = get_global_router(request)
            if global_router.project_manager and global_router.project_manager.db:
                db = global_router.project_manager.db
        if db is None:
            logger.error("[API] 数据库实例不可用，无法拉取聊天记录")
            return ok([], message="success")
        records = await db.get_chat_history(session_id)
        return ok(records, message="success")
    except Exception as e:
        logger.error(f"[API] 拉取聊天记录失败: {e}")
        raise HTTPException(status_code=500, detail="获取聊天记录失败")
@router.get("/subconscious", summary="潜意识流读取：获取发散引擎临时碎片")
async def get_subconscious_stream(
    request: Request
) -> dict[str, Any]:
    """
    扫描临时文件管理器的发散引擎目录，读取最新的临时碎片（如 brainstorm 或 massive_context）。
    流式渲染给前端悬浮窗。
    """
    from core.temp_manager import temp_manager
    import os
    import glob
    
    # 查找最新的 brainstorm 或 reflection 临时文件
    # ScopedTempManager 无 get_temp_dir()，改用 base_dir 下 scoped/divergence 子树
    temp_dir = temp_manager.base_dir / "scoped" / "divergence"
    search_pattern = os.path.join(str(temp_dir), "**", "*.txt")
    files = glob.glob(search_pattern, recursive=True)
    
    if not files:
        return ok({"content": "暂无活跃的发散思考..."}, message="success")
        
    # 按修改时间排序，取最新的一个
    latest_file = max(files, key=os.path.getmtime)
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            content = f.read()
            
        # 如果太长，截取尾部（因为是流式，前端可能想看最新思考）
        if len(content) > 5000:
            content = "..." + content[-5000:]
            
        return ok({"content": content, "file": os.path.basename(latest_file)}, message="success")
    except Exception as e:
        return fail(
            f"读取潜意识碎片失败: {e}",
            error_code="SUBCONSCIOUS_READ_ERROR",
            data={"content": f"读取潜意识碎片失败: {e}"},
        )
