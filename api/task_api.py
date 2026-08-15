"""
api/task_api.py — 批次1分段执行引擎独立任务接口路由（本批次新建）
================================================================
将原先散落在 api/projects.py 的任务查询接口迁移至此，
全部接口内部调用批次1 TaskManager 的封装方法，禁止直接写 SQL。

接口清单：
  GET  /api/v1/tasks/{task_id}            获取 CommandTask 信息（含进度）
  GET  /api/v1/tasks/{task_id}/segments   获取分段列表
  POST /api/v1/tasks/{task_id}/cancel     取消任务
  GET  /api/v1/tasks/{task_id}/audit-report  获取审计报告

兼容性说明：
  - 旧路由 /api/v1/tasks/{task_id}/status 仍保留在 api/projects.py，
    内部以 307 重定向代理到 GET /api/v1/tasks/{task_id}，
    本接口返回字段为旧接口的超集，前端无感知。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from services.task_manager import TaskManager as Batch1TaskManager
from api.deps import verify_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["Batch1 Task Engine"], dependencies=[Depends(verify_token)])


# ==========================================
# 依赖注入桩
# ==========================================
def get_batch1_task_manager(request: Request) -> Batch1TaskManager:
    """批次1引擎实例由 main.py 生命周期装配至 app.state。"""
    manager = getattr(request.app.state, "batch1_task_manager", None)
    if manager is None:
        logger.error("[task_api] 批次1任务引擎未挂载 (batch1_task_manager is None)")
        raise HTTPException(
            status_code=503,
            detail="批次1分段执行引擎未初始化，请检查系统装配",
        )
    return manager


# ==========================================
# 接口定义
# ==========================================
@router.get("/{task_id}", summary="获取 CommandTask 任务信息（含分段进度）")
async def get_task_detail(
    task_id: str,
    tm: Batch1TaskManager = Depends(get_batch1_task_manager),
) -> dict[str, Any]:
    """
    查询任务详情。返回字段为旧 /tasks/{task_id}/status 接口的超集：
    task_id / status / retry_count / priority / progress / 时间戳 / 错误信息等。
    """
    try:
        detail = await tm.get_task_detail(task_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="未找到对应的任务记录")
        return detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查询任务详情失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="任务查询失败")


@router.get("/{task_id}/segments", summary="获取任务的分段列表")
async def get_task_segments(
    task_id: str,
    tm: Batch1TaskManager = Depends(get_batch1_task_manager),
) -> dict[str, Any]:
    """查询任务全部分段元数据（大内容通过 output_path/result_available 标记，不整体搬运）。"""
    try:
        segments = await tm.get_task_segments(task_id)
        if segments is None:
            raise HTTPException(status_code=404, detail="未找到对应的任务记录")
        return {
            "task_id": task_id,
            "total": len(segments),
            "segments": segments,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查询分段列表失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="分段查询失败")


@router.post("/{task_id}/cancel", summary="取消任务")
async def cancel_task(
    task_id: str,
    tm: Batch1TaskManager = Depends(get_batch1_task_manager),
) -> dict[str, Any]:
    """取消排队中/运行中的任务（内部走 TaskManager 封装，无 RAW SQL）。"""
    try:
        cancelled = await tm.cancel_task(task_id)
        if not cancelled:
            raise HTTPException(
                status_code=404,
                detail="任务不存在或已处于终态，无法取消",
            )
        return {"task_id": task_id, "cancelled": True, "message": "任务取消指令已生效"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("取消任务失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="取消任务失败")


@router.get("/{task_id}/audit-report", summary="获取任务审计报告")
async def get_task_audit_report(
    task_id: str,
    tm: Batch1TaskManager = Depends(get_batch1_task_manager),
) -> dict[str, Any]:
    """返回执行耗时、分段成功率等统计（调用 TaskManager 封装方法）。"""
    try:
        report = await tm.generate_task_audit_report(task_id)
        if report is None:
            raise HTTPException(status_code=404, detail="未找到对应的任务记录")
        return report.model_dump(mode="json")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("生成审计报告失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="审计报告生成失败")
