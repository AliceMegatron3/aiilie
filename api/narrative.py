"""
api/narrative.py — 叙事结构层 API(阶段3)
==========================================
卷/章/拍纲要、节奏预算对账、伏笔账本的读写入口。
设计口径(讨论稿20260816 第七章):
- 解析(parse)与确认(confirm)分离:解析产物待作者确认后才作为生成约束(台账#23);
- 对账(reconcile)只报告偏差,不干预生成(软目标,台账#24);
- 伏笔回收期限为硬约束,超期由 audit 告警。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import get_db, verify_token
from models.narrative import (
    ChapterOutline,
    ForeshadowThread,
    PacingActuals,
    Volume,
)
from services.narrative_structure import NarrativeStructureService

router = APIRouter(dependencies=[Depends(verify_token)])


def _service(db=Depends(get_db)) -> NarrativeStructureService:
    return NarrativeStructureService(db)


# ── 卷 ────────────────────────────────────────────────────────

@router.get("/narrative/projects/{project_id}/volumes")
async def list_volumes(project_id: str, svc: NarrativeStructureService = Depends(_service)):
    await svc.initialize()
    return {"volumes": [v.model_dump() for v in await svc.get_volumes(project_id)]}


@router.post("/narrative/volumes")
async def save_volume(volume: Volume, svc: NarrativeStructureService = Depends(_service)):
    await svc.initialize()
    saved = await svc.save_volume(volume)
    return {"volume": saved.model_dump()}


# ── 章纲要:解析 → 确认分离 ────────────────────────────────────

class ParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


@router.post("/narrative/chapters/parse")
async def parse_outline(req: ParseRequest, svc: NarrativeStructureService = Depends(_service)):
    """自由文本 → 拍/预算/伏笔建议(预览,不落库;作者确认走 save+confirm)。"""
    result = svc.parse_outline(req.text)
    return {
        "beats": [b.model_dump() for b in result.beats],
        "budget": result.budget.model_dump() if result.budget else None,
        "thread_hints": [t.model_dump() for t in result.thread_hints],
        "needs_confirm": True,
    }


@router.post("/narrative/chapters")
async def save_chapter(chapter: ChapterOutline, svc: NarrativeStructureService = Depends(_service)):
    await svc.initialize()
    saved = await svc.save_chapter(chapter)
    return {"chapter": saved.model_dump()}


@router.post("/narrative/chapters/{chapter_id}/confirm-beats")
async def confirm_beats(chapter_id: str, svc: NarrativeStructureService = Depends(_service)):
    await svc.initialize()
    chapter = await svc.confirm_beats(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章纲要不存在")
    return {"chapter": chapter.model_dump()}


@router.get("/narrative/projects/{project_id}/chapters")
async def list_chapters(
    project_id: str,
    volume_id: str | None = None,
    svc: NarrativeStructureService = Depends(_service),
):
    await svc.initialize()
    chapters = await svc.get_chapters(project_id, volume_id)
    return {"chapters": [c.model_dump() for c in chapters]}


# ── 节奏对账 ──────────────────────────────────────────────────

class ActualsRequest(BaseModel):
    actuals: PacingActuals


@router.post("/narrative/chapters/{chapter_id}/actuals")
async def record_actuals(
    chapter_id: str, req: ActualsRequest, svc: NarrativeStructureService = Depends(_service)
):
    """登记章级实绩并即时对账(实绩来源:DataCard 聚合或手工)。"""
    await svc.initialize()
    chapter = await svc.get_chapter(chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章纲要不存在")
    chapter.actuals = req.actuals
    saved = await svc.save_chapter(chapter)
    variance = svc.reconcile_pacing(saved)
    return {"chapter": saved.model_dump(), "variance": variance.model_dump() if variance else None}


# ── 伏笔账本 ──────────────────────────────────────────────────

@router.post("/narrative/threads")
async def upsert_thread(thread: ForeshadowThread, svc: NarrativeStructureService = Depends(_service)):
    await svc.initialize()
    saved = await svc.upsert_thread(thread)
    return {"thread": saved.model_dump()}


class ThreadActionRequest(BaseModel):
    action: str = Field(pattern="^(advance|payoff)$")
    chapter_number: int = Field(default=0, ge=0)


@router.post("/narrative/threads/{thread_id}/actions")
async def thread_action(
    thread_id: str, req: ThreadActionRequest, svc: NarrativeStructureService = Depends(_service)
):
    await svc.initialize()
    if req.action == "advance":
        if req.chapter_number < 1:
            raise HTTPException(status_code=422, detail="advance 需要 chapter_number")
        thread = await svc.advance_thread(thread_id, req.chapter_number)
    else:
        thread = await svc.payoff_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="伏笔不存在")
    return {"thread": thread.model_dump()}


@router.get("/narrative/projects/{project_id}/threads/audit")
async def audit_threads(
    project_id: str, current_chapter: int = Query(ge=1),
    svc: NarrativeStructureService = Depends(_service),
):
    """未回收伏笔巡检:闲置章数与超期标记(硬约束违约)。"""
    await svc.initialize()
    items = await svc.audit_threads(project_id, current_chapter)
    overdue = [i for i in items if i.overdue]
    return {
        "current_chapter": current_chapter,
        "open_threads": len(items),
        "overdue": len(overdue),
        "items": [i.model_dump() for i in items],
    }


# ── P6:弧线模式端点(前端治理面板消费) ─────────────────────────

@router.get("/narrative/arcs")
async def list_arcs():
    """弧线模式清单(六出厂弧线+量化候选)。"""
    from services.arc_patterns import BUILTIN_ARC_PATTERNS

    return {
        "arcs": [
            {
                "pattern_id": p.pattern_id, "name": p.name, "description": p.description,
                "stages": [s.model_dump() for s in p.stages], "status": p.status,
                "variation_slots": p.variation_slots,
            }
            for p in BUILTIN_ARC_PATTERNS.values()
        ]
    }


class ApplyArcRequest(BaseModel):
    pattern_id: str
    n_chapters: int = Field(ge=1, le=500)
    start_number: int = Field(default=1, ge=1)


@router.post("/narrative/projects/{project_id}/volumes/{volume_id}/apply-arc")
async def apply_arc(
    project_id: str, volume_id: str, req: ApplyArcRequest,
    svc: NarrativeStructureService = Depends(_service),
):
    """套用弧线到卷:派生整卷章级预算+弧线绑定(已有章不覆盖拍纲)。"""
    from services.arc_patterns import apply_arc_to_volume, get_arc_pattern

    pattern = get_arc_pattern(req.pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="弧线模式不存在")
    await svc.initialize()
    saved = await apply_arc_to_volume(
        svc, project_id, volume_id, pattern,
        n_chapters=req.n_chapters, start_number=req.start_number,
    )
    return {
        "applied": len(saved), "pattern": pattern.name,
        "chapters": [
            {"chapter_number": c.chapter_number, "arc_stage": c.arc_stage,
             "budget": c.budget.model_dump() if c.budget else None}
            for c in saved
        ],
    }


@router.get("/narrative/projects/{project_id}/arc-variance")
async def arc_variance(
    project_id: str, volume_id: str | None = None,
    svc: NarrativeStructureService = Depends(_service),
):
    """弧线配方 vs 实际实绩偏差报告(只报告,不改预算)。"""
    await svc.initialize()
    return await svc.arc_variance_report(project_id, volume_id)


@router.post("/narrative/arcs/{pattern_id}/preview")
async def preview_arc(pattern_id: str, n_chapters: int = Query(ge=1, le=500)):
    """套用前预览派生的章级预算序列,避免误覆盖已有卷。"""
    from services.arc_patterns import get_arc_pattern

    pattern = get_arc_pattern(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="弧线模式不存在")
    budgets = pattern.derive_budget_sequence(n_chapters)
    return {
        "pattern_id": pattern.pattern_id,
        "name": pattern.name,
        "n_chapters": n_chapters,
        "sequence": [
            {
                "index": i + 1,
                "stage": pattern.stage_for_chapter(i, n_chapters).name,
                "conflict_intensity": b.conflict_intensity,
                "tempo": b.tempo,
            }
            for i, b in enumerate(budgets)
        ],
    }
