"""
api/router_storyboard.py — 补丁4：场景可视化分镜生成系统 REST API
==================================================================
feature.storyboard_enable 关闭时全部路由返回 400（新增能力，不破坏旧接口）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config_manager import config_manager
from core.feature_status import feature_disabled
from api.deps import verify_token
from services.storyboard import StoryboardPromptBuilder, StoryboardService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["Storyboard"], dependencies=[Depends(verify_token)])


def _require_feature() -> None:
    if not config_manager.get_bool("feature.storyboard_enable", False):
        raise HTTPException(
            status_code=400,
            detail="场景可视化分镜系统未启用（feature.storyboard_enable=false）",
        )


def get_storyboard_service(request: Request) -> StoryboardService:
    svc = getattr(request.app.state, "storyboard_service", None)
    if svc is None:
        svc = StoryboardService(
            request.app.state.project_manager,
            task_manager=getattr(request.app.state, "task_manager", None),
        )
        request.app.state.storyboard_service = svc
    return svc


class ExtractScenesRequest(BaseModel):
    text: str
    style_tags: list[str] = []


class GenerateRequest(BaseModel):
    scene_text: str
    style_tags: list[str] = []
    doc_id: str | None = None
    start: int | None = None
    end: int | None = None


@router.post("/storyboard/extract-scenes", summary="提取场景描写段落")
async def extract_scenes(req: ExtractScenesRequest) -> dict[str, Any]:
    _require_feature()
    builder = StoryboardPromptBuilder()
    passages = builder.extract_scene_passages(req.text)
    data = [
        {
            **p,
            "prompt": builder.build_prompt(p["snippet"], req.style_tags),
        }
        for p in passages
    ]
    return {"success": True, "data": data, "message": "success", "error_code": None}


@router.post("/projects/{project_id}/storyboard/generate", summary="提交分镜生图任务（批次1 GENERATE_IMAGE）")
async def generate_storyboard_image(
    project_id: str,
    req: GenerateRequest,
    svc: StoryboardService = Depends(get_storyboard_service),
) -> dict[str, Any]:
    _require_feature()
    # 阶段C：未接入真实生图 provider 时，不提交伪造任务，直接返回结构化 DISABLED。
    if not svc.image_generator_available:
        return feature_disabled("storyboard.image_generation", "provider_not_configured")
    text_range = (req.start, req.end) if req.start is not None and req.end is not None else None
    try:
        result = await svc.submit_generate_task(
            project_id=project_id,
            scene_text=req.scene_text,
            style_tags=req.style_tags,
            doc_id=req.doc_id,
            text_range=text_range,
        )
        return {"success": True, "data": result, "message": "生图任务已提交", "error_code": None}
    except Exception as e:
        logger.error("分镜生图任务提交失败: %s", e)
        raise HTTPException(status_code=500, detail="分镜生图任务提交失败")


@router.get("/projects/{project_id}/storyboard/anchors", summary="分镜锚点列表（段落↔图片）")
async def list_storyboard_anchors(
    project_id: str,
    doc_id: str | None = None,
    svc: StoryboardService = Depends(get_storyboard_service),
) -> dict[str, Any]:
    _require_feature()
    anchors = await svc.list_anchors(project_id, doc_id)
    return {"success": True, "data": anchors, "message": "success", "error_code": None}


@router.get("/projects/{project_id}/storyboard/anchors-by-range", summary="按文本段落查分镜图片")
async def anchors_by_range(
    project_id: str,
    doc_id: str,
    start: int,
    end: int,
    svc: StoryboardService = Depends(get_storyboard_service),
) -> dict[str, Any]:
    _require_feature()
    anchors = await svc.anchors_by_range(project_id, doc_id, start, end)
    return {"success": True, "data": anchors, "message": "success", "error_code": None}


@router.get("/projects/{project_id}/storyboard/anchors-by-image", summary="按图片查文本段落")
async def anchors_by_image(
    project_id: str,
    image_file: str,
    svc: StoryboardService = Depends(get_storyboard_service),
) -> dict[str, Any]:
    _require_feature()
    anchor = await svc.anchors_by_image(project_id, image_file)
    return {"success": True, "data": anchor, "message": "success", "error_code": None}


@router.delete("/projects/{project_id}/storyboard/anchors/{anchor_id}", summary="删除分镜锚点（含图片）")
async def delete_storyboard_anchor(
    project_id: str,
    anchor_id: str,
    svc: StoryboardService = Depends(get_storyboard_service),
) -> dict[str, Any]:
    _require_feature()
    ok = await svc.delete_anchor(project_id, anchor_id)
    if not ok:
        raise HTTPException(status_code=404, detail="锚点不存在")
    return {"success": True, "message": "锚点已删除", "error_code": None}


@router.get("/projects/{project_id}/storyboard/images/{file_name}", summary="读取分镜图片")
async def get_storyboard_image(
    project_id: str,
    file_name: str,
    svc: StoryboardService = Depends(get_storyboard_service),
):
    _require_feature()
    try:
        from core.path_resolver import safe_join

        path = safe_join(svc._storyboard_dir(project_id), file_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法文件名")
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片不存在")
    media = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    return FileResponse(path, media_type=media)
