"""统一知识 Ledger 与作者法则 API。

Ledger 查询是只读的；法则写入必须先通过 LawCompiler，且只保存草稿版本。
本路由不生成正文，也不触发导演或插件执行。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db, get_indexer, get_ledger_readiness, verify_token
from core.response import ok
from models.ledger import LawBranchRecord, LawRecord
from services.indexer import CardIndexer
from services.law_compiler import LawCompiler
from services.law_simulator import LawEventSimulator, LawSimulationError
from services.ledger_migration import list_migration_runs, migrate_legacy_cards
from services.ledger_reconciliation import reconcile_legacy_cards
from services.ledger_readiness import LedgerReadiness
from services.simpy_adapter import SimpyLawAdapter, SimpyUnavailableError
from services.novel_agent_skill_store import NovelAgentSkillStore

router = APIRouter(prefix="/ledger", tags=["Ledger"], dependencies=[Depends(verify_token)])


@router.post("/migrate/legacy-skills")
async def migrate_legacy_skills_api(
    db=Depends(get_db),
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    store = NovelAgentSkillStore(db, indexer=indexer)
    await store.initialize()
    return ok(await store.migrate_all_to_governance(), message="旧技能已提交统一治理候选池")


@router.get("/migrate/runs")
async def list_migration_runs_api(indexer: CardIndexer = Depends(get_indexer)) -> dict[str, Any]:
    return ok(await list_migration_runs(indexer))


@router.post("/migrate/legacy-cards")
async def migrate_legacy_cards_api(
    dry_run: bool = True,
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    return ok(await migrate_legacy_cards(indexer, dry_run=dry_run))


@router.get("/migrate/reconciliation")
async def reconcile_legacy_cards_api(indexer: CardIndexer = Depends(get_indexer)) -> dict[str, Any]:
    return ok(await reconcile_legacy_cards(indexer))


@router.get("/readiness")
async def ledger_readiness_api(readiness=Depends(get_ledger_readiness)) -> dict[str, Any]:
    return ok(await readiness.report())


@router.get("/documents")
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    return ok(await indexer.list_ledger_documents(limit=limit, offset=offset))


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    detail = await indexer.get_ledger_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Ledger 文档不存在")
    return ok(detail)


@router.get("/evidence")
async def list_evidence(
    document_id: str | None = None,
    passage_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    return ok(await indexer.list_ledger_evidence(
        document_id=document_id,
        passage_id=passage_id,
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    ))


@router.get("/metrics")
async def list_metrics(
    document_id: str | None = None,
    semantic_level: str | None = None,
    limit: int = 100,
    offset: int = 0,
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    return ok(await indexer.list_ledger_metrics(
        document_id=document_id,
        semantic_level=semantic_level,
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    ))


@router.get("/laws")
async def list_laws(
    layer: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    return ok(await indexer.list_ledger_laws(
        layer=layer,
        status=status,
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    ))


@router.get("/law-branches")
async def list_law_branches(indexer: CardIndexer = Depends(get_indexer)) -> dict[str, Any]:
    return ok(await indexer.list_law_branches())


@router.post("/law-branches")
async def create_law_branch(
    payload: LawBranchRecord,
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    try:
        return ok(await indexer.create_law_branch(payload), message="法则分支已创建")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/law-branches/{branch_id}/revisions")
async def list_law_revisions(branch_id: str, indexer: CardIndexer = Depends(get_indexer)) -> dict[str, Any]:
    return ok(await indexer.list_law_revisions(branch_id))


@router.post("/law-branches/{branch_id}/revisions")
async def append_law_revision(
    branch_id: str,
    payload: dict[str, Any],
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    operation = str(payload.get("operation", "override"))
    if operation not in {"override", "rollback", "snapshot"}:
        raise HTTPException(status_code=422, detail="operation 只能是 override/rollback/snapshot")
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=422, detail="snapshot 必须是对象")
    try:
        revision = await indexer.append_law_revision(branch_id, operation, snapshot, str(payload.get("note", "")))
        return ok(revision, message="法则分支已生成新 revision")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/laws/validate")
async def validate_law(payload: LawRecord) -> dict[str, Any]:
    return ok(LawCompiler().validate(payload))


@router.post("/laws/evaluate")
async def evaluate_law(payload: LawRecord, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    return ok(LawCompiler().evaluate(payload, variables))


@router.post("/laws/simulate-simpy")
async def simulate_laws_simpy(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        adapter = SimpyLawAdapter()
        if payload.get("capacity") is not None:
            result = adapter.run_resource_processes(
                payload.get("processes", []),
                capacity=int(payload.get("capacity", 1)),
                until=float(payload.get("until", 100)),
            )
        else:
            result = adapter.run_processes(payload.get("processes", []), float(payload.get("until", 100)))
        return ok(result)
    except (SimpyUnavailableError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/laws/simulate")
async def simulate_laws(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = LawEventSimulator().run(
            payload.get("initial_state", {}),
            payload.get("events", []),
            seed=int(payload.get("seed", 0)),
            max_steps=min(int(payload.get("max_steps", 100)), 1000),
        )
        return ok(result)
    except (TypeError, ValueError, LawSimulationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/laws")
async def save_law(
    payload: LawRecord,
    indexer: CardIndexer = Depends(get_indexer),
) -> dict[str, Any]:
    report = LawCompiler().validate(payload)
    if not report["valid"]:
        raise HTTPException(status_code=422, detail=report)
    saved = await indexer.save_ledger_law(payload)
    return ok(saved, message="法则草稿已保存；尚未执行或覆盖其他法则")
