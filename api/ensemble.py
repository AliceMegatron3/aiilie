"""
api/ensemble.py — 群像层 API(P7,前端治理面板消费)
====================================================
- GET  /ensemble/projects/{pid}/relationships  当前关系账本
- GET  /ensemble/projects/{pid}/tracks         生活轨道清单
- POST /ensemble/projects/{pid}/events/{eid}/close  事件清算→回摆提醒
"""
from fastapi import APIRouter, Depends

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
