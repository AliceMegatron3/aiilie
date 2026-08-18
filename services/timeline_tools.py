"""受控执行 — 时间线域默认 tool handler（V0.4 第一批工具：timeline.event_create）。

方向报告「四、第一批工具」含"创建时间轴事件"。本模块提供真实项目能力接线：
`timeline.event_create` 调用既有 `TimelineService.add_event`（挂载在 AuthorProject
时间线上，走 project_manager 持久化），并把事件字段从 handler 参数构造为
`TimelineEvent`。安全不变量：
- `feature.timeline_enable` 关闭时返回结构化 `DISABLED`（fail-closed，不伪装成功）；
- 必填参数（project_id / timeline_id / title）缺失即抛错，不静默降级。
"""
from __future__ import annotations

from typing import Any

from core.config_manager import config_manager


def make_timeline_event_create_handler(project_manager: Any | None = None) -> Any:
    """`timeline.event_create` 默认 handler：在真实项目能力上创建时间轴事件。

    `project_manager` 可为 app.state.project_manager（运行时注入）；未注入时
    `TimelineService` 以 None 构造，仅在 feature 关闭或参数缺失时安全返回/抛错。
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        # 特性门 fail-closed：未启用时间轴能力 → 结构化 DISABLED，不伪装成功
        if not config_manager.get_bool("feature.timeline_enable", False):
            return {
                "status": "DISABLED",
                "feature": "timeline",
                "reason": "feature_timeline_disabled",
            }
        project_id = str(args.get("project_id", "") or "").strip()
        timeline_id = str(args.get("timeline_id", "") or "").strip()
        title = str(args.get("title", "") or "").strip()
        if not project_id or not timeline_id or not title:
            raise ValueError("timeline.event_create 需要 project_id / timeline_id / title")

        from models.timeline import TimelineEvent
        from services.timeline_service import TimelineService

        event = TimelineEvent(
            title=title,
            description=str(args.get("description", "") or ""),
            story_time=args.get("story_time"),
            track_index=int(args.get("track_index", 0) or 0),
            position_x=float(args.get("position_x", 0.0) or 0.0),
            character_states=dict(args.get("character_states") or {}),
            event_type=str(args.get("event_type", "PLOT") or "PLOT"),
        )
        created = await TimelineService(project_manager).add_event(
            project_id, timeline_id, event
        )
        return {"status": "OK", "event_id": created.event_id, "event": created.model_dump()}

    return handler


__all__ = ["make_timeline_event_create_handler"]
