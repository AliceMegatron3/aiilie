"""受控执行 — 任务域默认 tool handler（V0.4 第一批工具：task.quantize_progress）。

方向报告「四、第一批工具」含"查询量化进度"。本模块提供真实项目能力接线：
`task.quantize_progress` 调用既有 `TaskManager.get_task_detail`（返回
status / progress{total,completed,percent} / error_message 等），作为量化任务的
进度视图。安全不变量：
- 未注入 task_manager → 结构化 `DISABLED`（fail-closed，不伪装进度）；
- 任务不存在 → 抛错（不伪造"已完成"）。
"""
from __future__ import annotations

from typing import Any


def make_quantize_progress_handler(task_manager: Any | None = None) -> Any:
    """`task.quantize_progress` 默认 handler：查询量化任务进度。

    `task_manager` 可为 app.state.batch1_task_manager（与量化提交同一调度器）；
    未接线时返回结构化 DISABLED。
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        task_id = str(args.get("task_id", "") or "").strip()
        if not task_id:
            raise ValueError("task.quantize_progress 需要 task_id")
        if task_manager is None:
            return {
                "status": "DISABLED",
                "feature": "task.quantize_progress",
                "reason": "task_manager_not_wired",
            }
        detail = await task_manager.get_task_detail(task_id)
        if detail is None:
            raise ValueError(f"量化任务不存在: {task_id}")
        return {
            "task_id": task_id,
            "status": detail.get("status"),
            "progress": detail.get("progress"),
            "error_message": detail.get("error_message"),
        }

    return handler


__all__ = ["make_quantize_progress_handler"]
