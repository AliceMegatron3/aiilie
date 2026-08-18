"""
api/router_timeline.py — 补丁1：非线性叙事时间轴系统 REST API
==============================================================
feature.timeline_enable 关闭时全部路由返回 400（新增能力，不破坏旧接口）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.config_manager import config_manager
from api.deps import verify_token
from core.response import ok
from models.timeline import Timeline, TimelineEvent
from services.timeline_service import TimelineService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["Timeline"], dependencies=[Depends(verify_token)])


def _require_feature() -> None:
    if not config_manager.get_bool("feature.timeline_enable", False):
        raise HTTPException(
            status_code=400,
            detail="非线性叙事时间轴系统未启用（feature.timeline_enable=false）",
        )


def get_timeline_service(request: Request) -> TimelineService:
    return TimelineService(request.app.state.project_manager)


# ── 时间线 CRUD ─────────────────────────────────────────────────

@router.get("/projects/{project_id}/timelines", summary="获取项目全部时间线")
async def list_timelines(
    project_id: str,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        timelines = await svc.list_timelines(project_id)
        return ok([t.model_dump() for t in timelines], message="success")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/projects/{project_id}/timelines", summary="创建时间线")
async def create_timeline(
    project_id: str,
    timeline: Timeline,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        created = await svc.create_timeline(project_id, timeline)
        return ok(created.model_dump(), message="时间线已创建")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/timelines/{timeline_id}", summary="获取单条时间线")
async def get_timeline(
    project_id: str,
    timeline_id: str,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        timeline = await svc.get_timeline(project_id, timeline_id)
        return ok(timeline.model_dump(), message="success")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/projects/{project_id}/timelines/{timeline_id}", summary="更新时间线")
async def update_timeline(
    project_id: str,
    timeline_id: str,
    patch: Timeline,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        updated = await svc.update_timeline(project_id, timeline_id, patch)
        return ok(updated.model_dump(), message="时间线已更新")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/projects/{project_id}/timelines/{timeline_id}", summary="删除时间线")
async def delete_timeline(
    project_id: str,
    timeline_id: str,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        await svc.delete_timeline(project_id, timeline_id)
        return ok({"deleted": True}, message="时间线已删除")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 事件 CRUD ───────────────────────────────────────────────────

@router.post(
    "/projects/{project_id}/timelines/{timeline_id}/events", summary="新增时间线事件"
)
async def add_event(
    project_id: str,
    timeline_id: str,
    event: TimelineEvent,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        created = await svc.add_event(project_id, timeline_id, event)
        return ok(created.model_dump(), message="事件已添加")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put(
    "/projects/{project_id}/timelines/{timeline_id}/events/{event_id}", summary="更新时间线事件"
)
async def update_event(
    project_id: str,
    timeline_id: str,
    event_id: str,
    patch: TimelineEvent,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        updated = await svc.update_event(project_id, timeline_id, event_id, patch)
        return ok(updated.model_dump(), message="事件已更新")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete(
    "/projects/{project_id}/timelines/{timeline_id}/events/{event_id}", summary="删除时间线事件"
)
async def delete_event(
    project_id: str,
    timeline_id: str,
    event_id: str,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        await svc.delete_event(project_id, timeline_id, event_id)
        return ok({"deleted": True}, message="事件已删除")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 冲突检测 ────────────────────────────────────────────────────

@router.get(
    "/projects/{project_id}/timelines/{timeline_id}/conflicts", summary="检测时间线冲突"
)
async def check_timeline_conflicts(
    project_id: str,
    timeline_id: str,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        report = await svc.check_conflicts(project_id, timeline_id)
        return ok(report.model_dump(), message="success")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/projects/{project_id}/timeline-conflicts", summary="检测项目全部时间线冲突"
)
async def check_all_timeline_conflicts(
    project_id: str,
    svc: TimelineService = Depends(get_timeline_service),
) -> dict[str, Any]:
    _require_feature()
    try:
        reports = await svc.check_all_conflicts(project_id)
        return ok([r.model_dump() for r in reports], message="success")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
