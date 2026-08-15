"""
api/router_tts.py — 补丁2：沉浸式角色语调 TTS 与校对系统 REST API
==================================================================
feature.tts_enable 关闭时全部路由返回 400（新增能力，不破坏旧接口）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config_manager import config_manager
from api.deps import verify_token
from services.tts import DialogueSegmenter, tts_dispatcher

logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["TTS"], dependencies=[Depends(verify_token)])


def _require_feature() -> None:
    if not config_manager.get_bool("feature.tts_enable", False):
        raise HTTPException(
            status_code=400,
            detail="角色语调 TTS 系统未启用（feature.tts_enable=false）",
        )


class SegmentRequest(BaseModel):
    text: str
    known_characters: list[str] = []


class SynthesizeRequest(BaseModel):
    text: str
    voice: str | None = None
    engine: str | None = None


class OOCFeedbackRequest(BaseModel):
    project_id: str
    doc_id: str
    character: str
    content: str


def _get_project_manager(request: Request):
    return request.app.state.project_manager


@router.post("/tts/segment", summary="分割旁白/对话并识别角色")
async def segment_dialogue(req: SegmentRequest) -> dict[str, Any]:
    _require_feature()
    segments = DialogueSegmenter().segment(req.text, req.known_characters)
    return {"success": True, "data": segments, "message": "success", "error_code": None}


@router.post("/tts/synthesize", summary="合成角色语音")
async def synthesize_tts(req: SynthesizeRequest) -> dict[str, Any]:
    _require_feature()
    try:
        result = await tts_dispatcher.synthesize(req.text, req.voice, req.engine)
        return {
            "success": True,
            "data": {**result, "audio_url": f"/api/v1/tts/audio/{result['file_name']}"},
            "message": "success",
            "error_code": None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("TTS 合成失败: %s", e)
        raise HTTPException(status_code=500, detail=f"TTS 合成失败: {e}")


@router.get("/tts/audio/{file_name}", summary="读取合成音频文件")
async def get_tts_audio(file_name: str) -> FileResponse:
    _require_feature()
    try:
        path = tts_dispatcher.resolve_audio_path(file_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法文件名")
    if not path.exists():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return FileResponse(path, media_type="audio/wav")


@router.post("/tts/ooc-feedback", summary="提交角色 OOC 校对反馈（写入批次3学习系统）")
async def submit_ooc_feedback(
    req: OOCFeedbackRequest,
    request: Request,
) -> dict[str, Any]:
    _require_feature()
    learning_engine = getattr(request.app.state, "learning_engine", None)
    try:
        result = await tts_dispatcher.submit_ooc_feedback(
            project_id=req.project_id,
            doc_id=req.doc_id,
            character=req.character,
            content=req.content,
            learning_engine=learning_engine,
        )
        return {"success": True, "data": result, "message": "反馈已受理", "error_code": None}
    except Exception as e:
        logger.error("OOC 反馈提交失败: %s", e)
        raise HTTPException(status_code=500, detail="OOC 反馈提交失败")


@router.get("/projects/{project_id}/voice-profiles", summary="获取项目角色音色映射")
async def get_voice_profiles(
    project_id: str,
    pm=Depends(_get_project_manager),
) -> dict[str, Any]:
    _require_feature()
    project = await pm.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {
        "success": True,
        "data": project.character_voice_profiles,
        "message": "success",
        "error_code": None,
    }


class VoiceProfilesRequest(BaseModel):
    character_voice_profiles: dict[str, str]


@router.put("/projects/{project_id}/voice-profiles", summary="更新项目角色音色映射")
async def update_voice_profiles(
    project_id: str,
    req: VoiceProfilesRequest,
    pm=Depends(_get_project_manager),
) -> dict[str, Any]:
    _require_feature()
    project = await pm.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.character_voice_profiles = req.character_voice_profiles
    await pm.update_project(project)
    return {"success": True, "message": "角色音色映射已更新", "error_code": None}
