"""
api/ensemble.py — 群像层 API(P7,前端治理面板消费)
====================================================
- GET  /ensemble/projects/{pid}/relationships  当前关系账本
- GET  /ensemble/projects/{pid}/tracks         生活轨道清单
- POST /ensemble/projects/{pid}/events/{eid}/close  事件清算→回摆提醒
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_db, verify_token
from services.ensemble import EnsembleService

router = APIRouter(dependencies=[Depends(verify_token)])

_ensemble_instances: dict[str, EnsembleService] = {}


async def _service(project_id: str, db=Depends(get_db)) -> EnsembleService:
    svc = _ensemble_instances.get(project_id)
    if svc is None:
        svc = EnsembleService(db)
        await svc.initialize()
        _ensemble_instances[project_id] = svc
    return svc


@router.get("/ensemble/projects/{project_id}/relationships")
async def list_relationships(project_id: str, svc: EnsembleService = Depends(_service)):
    return {"relationships": await svc.current_relationships(project_id)}


@router.get("/ensemble/projects/{project_id}/tracks")
async def list_tracks(project_id: str, svc: EnsembleService = Depends(_service)):
    tracks = await svc.get_tracks(project_id)
    return {
        "tracks": [
            {
                "character_id": t.character_id, "name": t.name, "position": t.position,
                "livelihood": t.livelihood, "needs": t.needs, "updated_chapter": t.updated_chapter,
            }
            for t in tracks
        ]
    }


@router.post("/ensemble/projects/{project_id}/events/{event_id}/close")
async def close_event(project_id: str, event_id: str, svc: EnsembleService = Depends(_service)):
    """事件清算:挂账按余额深浅回摆结晶,派系解散,返回回摆提醒清单。"""
    reminders = await svc.close_event(project_id, event_id)
    return {"reminders": reminders, "count": len(reminders)}


# ── 录入端点(债4:让群像数据可冷启动) ─────────────────────────

class TrackUpsertRequest(BaseModel):
    character_id: str = Field(min_length=1)
    name: str = ""
    aliases: list[str] = Field(default_factory=list)
    position: str = ""
    livelihood: str = ""
    attachments: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    updated_chapter: int = Field(default=1, ge=1)


@router.post("/ensemble/projects/{project_id}/tracks")
async def upsert_track(
    project_id: str, req: TrackUpsertRequest, svc: EnsembleService = Depends(_service)
):
    """录入/更新角色生活轨道(在场识别与矛盾巡检的匹配来源)。"""
    from models.ensemble import LifeTrack

    track = LifeTrack(project_id=project_id, **req.model_dump())
    saved = await svc.upsert_track(track)
    return {"track": saved.model_dump()}


class BaselineRequest(BaseModel):
    character_a: str = Field(min_length=1)
    character_b: str = Field(min_length=1)
    baseline: int = Field(ge=-100, le=100)


@router.post("/ensemble/projects/{project_id}/relationships/baseline")
async def set_relationship_baseline(
    project_id: str, req: BaselineRequest, svc: EnsembleService = Depends(_service)
):
    """设定关系基线(情感账户余额);事件挂账与回摆仍走各自入口。"""
    if req.character_a == req.character_b:
        raise HTTPException(status_code=422, detail="不能为同一角色设定关系基线")
    entry = await svc.set_baseline(project_id, req.character_a, req.character_b, req.baseline)
    return {"pair": entry.pair, "baseline": entry.baseline, "current": entry.current()}


class EventDeltaRequest(BaseModel):
    character_a: str = Field(min_length=1)
    character_b: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    delta: int = Field(ge=-200, le=200)
    reason: str = ""


@router.post("/ensemble/projects/{project_id}/relationships/event-delta")
async def record_event_delta(
    project_id: str, req: EventDeltaRequest, svc: EnsembleService = Depends(_service)
):
    """登记事件挂账(大事件期间的临时对位),清算时按深浅回摆。"""
    if req.character_a == req.character_b:
        raise HTTPException(status_code=422, detail="不能为同一角色登记事件挂账")
    entry = await svc.record_event_delta(
        project_id, req.character_a, req.character_b, req.event_id, req.delta, req.reason
    )
    return {"pair": entry.pair, "current": entry.current(), "baseline": entry.baseline}


class VoiceUpsertRequest(BaseModel):
    character_id: str = Field(min_length=1)
    name: str = ""
    speech_habits: str = ""
    decision_style: str = ""
    pov_texture: str = ""


@router.post("/ensemble/projects/{project_id}/voices")
async def upsert_voice(
    project_id: str, req: VoiceUpsertRequest, svc: EnsembleService = Depends(_service)
):
    """录入角色声纹(生成时按在场角色注入,对白分口吻)。"""
    from models.ensemble import VoiceCard

    voice = VoiceCard(project_id=project_id, **req.model_dump())
    saved = await svc.upsert_voice(voice)
    return {"voice": saved.model_dump()}
