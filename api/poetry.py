"""古诗确定性研究 API：分析（增强）/ 作品持久化 / 生成 / 改写 / 评测。

保持 POST /poetry/analyze 兼容（原 work/edition/lines/evaluation 字段不变），
并叠加音韵学结果（韵部/平仄/格律/典故/韵部候选）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_db, get_llm_client, verify_token
from core.response import ok
from models.poetry import PoetryEdition, PoetryWork
from services.poetry_analyzer import analyze_work
from services.poetry_evaluator import run_evaluation
from services.poetry_generator import generate_poem, rewrite_poem
from services.poetry_store import PoetryStore

router = APIRouter(prefix="/poetry", tags=["Poetry Research"], dependencies=[Depends(verify_token)])


class PoetryAnalyzeRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    author: str = ""
    dynasty: str = ""
    genre: str = ""
    style: str = ""
    text: str = Field(min_length=1, max_length=100000)
    source_uri: str = ""
    source_revision: str = ""
    license_expression: str = "NOASSERTION"


class PoetrySaveRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    author: str = ""
    dynasty: str = ""
    style: str = ""
    text: str = Field(min_length=1, max_length=100000)
    work_id: str = ""
    source_document_id: str = ""
    passage_id: str = ""
    extraction_run_id: str = ""


class PoetryGenerateRequest(BaseModel):
    style: str = Field(default="五绝")
    theme: str = Field(default="春")
    rhyme: str = ""
    n: int = Field(default=4, ge=1, le=8)
    use_llm: bool = False


class PoetryRewriteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100000)
    target: str = Field(default="prosody_fix")
    style: str = ""


def _store(db) -> PoetryStore:
    return PoetryStore(db)


@router.post("/analyze")
async def analyze_poetry(payload: PoetryAnalyzeRequest) -> dict[str, Any]:
    edition = PoetryEdition(
        content_hash=hashlib.sha256(payload.text.encode("utf-8")).hexdigest(),
        source_uri=payload.source_uri,
        source_revision=payload.source_revision,
        license_expression=payload.license_expression,
        license_status="known" if payload.license_expression != "NOASSERTION" else "unknown",
    )
    work = PoetryWork(
        title=payload.title,
        author=payload.author,
        dynasty=payload.dynasty,
        genre=payload.genre,
        edition_ids=[edition.edition_id],
    )
    lines, evaluation = analyze_work(work, payload.text)
    store = PoetryStore(None)
    enhanced = store.analyze_work(payload.text, payload.style or payload.genre or None)
    return ok({
        "work": work.model_dump(mode="json"),
        "edition": edition.model_dump(mode="json"),
        "lines": [line.model_dump(mode="json") for line in lines],
        "evaluation": evaluation.model_dump(mode="json"),
        # 音韵学增强（新增，不破坏既有字段）
        "phonology": enhanced["prosody"],
        "meter": enhanced["meter"],
        "rhyme_candidates": enhanced["rhyme_candidates"],
        "allusions": enhanced["allusions"],
        "detected_style": enhanced["style"],
    }, message="诗词确定性分析完成；含平水韵/平仄/格律/典故（实用近似，未调用模型）")


@router.post("/works")
async def save_poetry_work(payload: PoetrySaveRequest, db=Depends(get_db)) -> dict[str, Any]:
    store = _store(db)
    saved = await store.save_work(
        title=payload.title,
        author=payload.author,
        dynasty=payload.dynasty,
        style=payload.style,
        text=payload.text,
        work_id=payload.work_id or None,
        source_document_id=payload.source_document_id,
        passage_id=payload.passage_id,
        extraction_run_id=payload.extraction_run_id,
    )
    return ok(saved, message="作品与版本已保存（幂等建表，未触碰真实数据）")


@router.get("/works")
async def list_poetry_works(db=Depends(get_db)) -> dict[str, Any]:
    store = _store(db)
    works = await store.list_works()
    return ok({"works": works, "count": len(works)}, message="作品列表已返回")


@router.get("/works/{work_id}")
async def get_poetry_work(work_id: str, db=Depends(get_db)) -> dict[str, Any]:
    store = _store(db)
    work = await store.get_work(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"作品不存在: {work_id}")
    return ok(work, message="作品与版本（含 provenance）已返回")


@router.post("/generate")
async def generate_poetry(payload: PoetryGenerateRequest, llm_client=Depends(get_llm_client)) -> dict[str, Any]:
    # 方向报告 B 类：诗词 use_llm 之前未传 LLM client，实际悄然回退启发式。
    # 现在显式注入依赖；若 use_llm=True 但无可用 LLM client，则在结果中如实标注已回退，
    # 不再让调用方误以为走了大模型。
    result = generate_poem(
        style=payload.style,
        theme=payload.theme,
        rhyme=payload.rhyme or None,
        n=payload.n,
        llm_client=llm_client,
        use_llm=payload.use_llm,
    )
    engine = result.get("engine", "heuristic")
    if payload.use_llm and engine != "llm":
        message = "已请求 LLM 生成，但未配置可用 llm_client，实际回退启发式字库填充"
    elif engine == "llm":
        message = "诗词已由配置的 LLM 生成"
    else:
        message = "诗词已生成（启发式字库填充，未调用大模型）"
    return ok(result, message=message)


@router.post("/rewrite")
async def rewrite_poetry(payload: PoetryRewriteRequest) -> dict[str, Any]:
    result = rewrite_poem(text=payload.text, target=payload.target, style=payload.style or None)
    return ok(result, message="格律改写建议已生成（基于 validate_meter/evaluate_prosody）")


@router.get("/evaluate")
async def evaluate_poetry() -> dict[str, Any]:
    result = run_evaluation()
    return ok(result, message="评测集跑分完成（临时评测集，未触碰真实数据）")
