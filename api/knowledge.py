"""
api/knowledge.py — 知识补全与缺口 API(P3降级版)
==================================================
- GET  /knowledge/gaps                 缺口聚账报告(检索未命中聚合)
- GET  /knowledge/proposals            补全建议清单(思考模式/反思可提交)
- POST /knowledge/propose              登记补全建议(PENDING,审而非发)
- POST /knowledge/complete             作者确认执行补全→LLM降级知识→draft卡
升级位:搜索API凭证到位后 complete 走真实时 SearchProvider,流程不变。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_indexer, get_model_dispatcher, verify_token
from services.knowledge_completion import (
    aggregate_gaps,
    complete_via_llm,
    create_completion_card,
    list_proposals,
    propose_completion,
)

router = APIRouter(dependencies=[Depends(verify_token)])


def _read_miss_log() -> list[dict]:
    from services.knowledge_completion import _read_jsonl, _default_path

    return _read_jsonl(_default_path("retrieval_miss.jsonl"))


@router.get("/knowledge/gaps")
async def knowledge_gaps(project_id: str | None = None):
    records = _read_miss_log()
    if project_id:
        records = [r for r in records if r.get("project_id") == project_id]
    return aggregate_gaps(records)


@router.get("/knowledge/proposals")
async def knowledge_proposals(project_id: str | None = None):
    return {"proposals": list_proposals(project_id)}


class KnowledgeProposeRequest(BaseModel):
    project_id: str
    topic: str = Field(min_length=1, max_length=200)
    description: str = ""
    source: str = "manual"   # manual / deep_think / knowledge_reflection


@router.post("/knowledge/propose")
async def knowledge_propose(req: KnowledgeProposeRequest):
    """登记补全建议(PENDING)。deep_think 思考收尾与知识反思的提案入口。"""
    record = propose_completion(
        req.project_id, req.topic, req.description, source=req.source
    )
    return {"proposal": record}


class KnowledgeCompleteRequest(BaseModel):
    project_id: str
    topic: str = Field(min_length=1, max_length=200)
    description: str = ""


@router.post("/knowledge/complete")
async def knowledge_complete(
    req: KnowledgeCompleteRequest,
    dispatcher=Depends(get_model_dispatcher),
    indexer=Depends(get_indexer),
):
    """作者确认执行补全:LLM降级知识(或升级后搜索)→draft卡强制过审。"""
    if dispatcher is None:
        raise HTTPException(status_code=503, detail="模型调度器未装配,无法执行补全")
    try:
        content = await complete_via_llm(dispatcher, req.topic, req.description)
    except ValueError as exc:
        logger.warning("知识补全失败: %s", exc)
        from core.errors import http_error
        raise http_error(502, "KNOWLEDGE_COMPLETION_FAILED")
    card_id = await create_completion_card(
        indexer, req.project_id, req.topic, content, source_tag="llm_completion"
    )
    return {
        "topic": req.topic,
        "content": content[:500],
        "card_id": card_id,
        "card_status": "draft",
        "needs_author_review": True,
    }
