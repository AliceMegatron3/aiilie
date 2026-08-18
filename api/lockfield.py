"""
api/lockfield.py — 世界观锁定场 API(阶段4)
============================================
锁定场配置(单场+版本演化)、物化 must 集、偏离点登记的读写入口。
口径(讨论稿20260816 第六章):偏离登记为"检测建议+作者确认"——
propose 产生 PENDING,confirm 之后才入册生效。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.deps import get_db, get_indexer, verify_token
from core.response import ok
from models.lockfield import DivergenceRecord, LockFieldConfig
from services.lockfield import LockFieldService

router = APIRouter(dependencies=[Depends(verify_token)])


def _service(request: Request, db=Depends(get_db), indexer=Depends(get_indexer)) -> LockFieldService:
    """优先复用 bootstrap 单例(共享 must 集缓存);缺失时按请求构建。"""
    svc = getattr(request.app.state, "lockfield_service", None)
    if svc is not None:
        return svc
    return LockFieldService(db, indexer)


@router.get("/lockfield/projects/{project_id}")
async def get_lockfield(project_id: str, svc: LockFieldService = Depends(_service)):
    await svc.initialize()
    config = await svc.get_field(project_id)
    return ok({"lockfield": config.model_dump() if config else None})


@router.post("/lockfield/projects/{project_id}")
async def save_lockfield(
    project_id: str, config: LockFieldConfig, svc: LockFieldService = Depends(_service)
):
    await svc.initialize()
    if config.project_id and config.project_id != project_id:
        raise HTTPException(status_code=422, detail="project_id 与路径不一致")
    config.project_id = project_id
    saved = await svc.save_field(config)
    return ok({"lockfield": saved.model_dump()})


@router.get("/lockfield/projects/{project_id}/must-set")
async def get_must_set(
    project_id: str, force_rebuild: bool = False,
    svc: LockFieldService = Depends(_service),
):
    """物化 must 集(生成时的环境锁定集;按版本缓存,确定性排序保前缀缓存)。"""
    await svc.initialize()
    config = await svc.get_field(project_id)
    if config is None:
        raise HTTPException(status_code=404, detail="该项目尚未建立锁定场")
    must_set = await svc.materialize_must_set(config, force_rebuild=force_rebuild)
    return ok({
        "must_set": {
            "field_id": must_set.field_id,
            "version": must_set.version,
            "total": must_set.total,
            "digest": must_set.digest,
            "card_ids": must_set.card_ids,
            "built_at": must_set.built_at,
        }
    })


@router.get("/lockfield/projects/{project_id}/versions")
async def list_versions(project_id: str, svc: LockFieldService = Depends(_service)):
    await svc.initialize()
    config = await svc.get_field(project_id)
    if config is None:
        return ok({"versions": []})
    versions = await svc.list_versions(config.field_id)
    return ok({"versions": [v.model_dump() for v in versions]})


@router.post("/lockfield/projects/{project_id}/rollback")
async def rollback(project_id: str, target_version: int, svc: LockFieldService = Depends(_service)):
    await svc.initialize()
    config = await svc.get_field(project_id)
    if config is None:
        raise HTTPException(status_code=404, detail="该项目尚未建立锁定场")
    rolled = await svc.rollback_to_version(config.field_id, target_version)
    return ok({"lockfield": rolled.model_dump() if rolled else None})


@router.post("/lockfield/divergences")
async def propose_divergence(
    record: DivergenceRecord, svc: LockFieldService = Depends(_service)
):
    """登记偏离建议(PENDING,待作者确认)。"""
    await svc.initialize()
    saved = await svc.propose_divergence(record)
    return ok({"divergence": saved.model_dump()})


class DivergenceActionRequest(BaseModel):
    action: str


@router.post("/lockfield/divergences/{divergence_id}/actions")
async def divergence_action(
    divergence_id: str, req: DivergenceActionRequest,
    svc: LockFieldService = Depends(_service),
):
    await svc.initialize()
    if req.action == "confirm":
        record = await svc.confirm_divergence(divergence_id)
    elif req.action == "retract":
        record = await svc.retract_divergence(divergence_id)
    else:
        raise HTTPException(status_code=422, detail="action 仅支持 confirm/retract")
    if record is None:
        raise HTTPException(status_code=404, detail="偏离记录不存在")
    return ok({"divergence": record.model_dump()})


@router.get("/lockfield/projects/{project_id}/divergences")
async def list_divergences(
    project_id: str, status: str | None = None,
    svc: LockFieldService = Depends(_service),
):
    await svc.initialize()
    from models.lockfield import DivergenceStatus

    status_enum = DivergenceStatus(status) if status else None
    records = await svc.list_divergences(project_id, status_enum)
    return ok({"divergences": [r.model_dump() for r in records]})
