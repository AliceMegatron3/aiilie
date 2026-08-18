"""
api/library.py — 知识库与量化系统的 FastAPI 路由
===================================================
提供 RESTful 接口，对外暴露批次2的卡片检索、详情读取及量化任务调度能力。

架构优化（本批次）：
- 依赖注入：废弃 GlobalDependencies 全局类属性单例，
  改为 FastAPI Depends + request.app.state（见 api/deps.py）。
- 上传安全：扩展名白名单 + 大小上限（默认 20MB）。
- 响应结构：统一 {success, data, message, error_code}。
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from api.deps import get_db, get_indexer, get_ledger_read_facade, get_ledger_repository, get_quantifier, verify_token
from core.config_manager import config_manager
from core.database import DatabaseManager
from core.exceptions import (
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from core.path_resolver import get_app_data_dir
from core.response import ok
from services.indexer import CardIndexer
from services.ledger_repository import LedgerRepository
from services.quantifier import BookQuantifier
from services.parser import ParserFactory, ParserLimitError, load_parser_limits

logger = logging.getLogger(__name__)


def _filter_cards_by_quadrant(
    cards: list[Any],
    min_utility: float | None = None,
    min_entropy: float | None = None,
) -> list[Any]:
    """四象限阈值过滤：按 utility_score / entropy_score（>= 阈值）过滤卡片结果。

    Batch 1：卡片搜索结果的可选阈值过滤，纯确定性内存过滤，不做 LLM 裁决。
    ledger 投影结果与 legacy 卡片行结构不完全一致，统一兼容 dict 与带 detail 的包装。
    """
    if min_utility is None and min_entropy is None:
        return cards

    def _item(c: Any) -> dict[str, Any]:
        if isinstance(c, dict):
            # 优先取 detail（ledger 投影常见形状）；无 detail 分数则回退顶层。
            d = c.get("detail")
            if isinstance(d, dict) and ("utility_score" in d or "entropy_score" in d):
                return d
            if "utility_score" in c or "entropy_score" in c or "card_id" in c:
                return c
        return c if isinstance(c, dict) else {}

    kept: list[Any] = []
    for c in cards:
        item = _item(c)
        u = float(item.get("utility_score") or 0)
        e = float(item.get("entropy_score") or 0)
        if min_utility is not None and u < min_utility:
            continue
        if min_entropy is not None and e < min_entropy:
            continue
        kept.append(c)
    return kept


def _split_text_passages(text: str) -> list[tuple[int, str]]:
    passages: list[tuple[int, str]] = []
    cursor = 0
    for block in text.split("\n\n"):
        value = block.strip()
        start = text.find(value, cursor) if value else cursor
        if value:
            passages.append((start, value))
            cursor = start + len(value)
        else:
            cursor += len(block) + 2
    return passages


def _write_book_metadata(books_dir: Path, book_id: str, values: dict[str, Any]) -> None:
    """原子更新书籍元数据，避免导入标题/解析信息丢失。"""
    meta_file = books_dir / "books_metadata.json"
    metadata: dict[str, Any] = {}
    if meta_file.exists():
        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
    current = dict(metadata.get(book_id, {}))
    current.update(values)
    current["updated_at"] = __import__("time").time()
    metadata[book_id] = current
    tmp_file = meta_file.with_suffix(".json.tmp")
    tmp_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_file.replace(meta_file)


# 初始化路由
router = APIRouter(prefix="/library", tags=["Library"])

# ── 上传安全配置（可通过 config 覆盖） ──────────────────────────────
_UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # 默认 20MB
_ALLOWED_EXTENSIONS = {".txt", ".md", ".docx", ".doc", ".pdf", ".epub"}


def _validate_upload(file: UploadFile) -> None:
    """上传安全校验：扩展名白名单 + 大小上限。"""
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(sorted(_ALLOWED_EXTENSIONS))

    max_bytes = int(
        config_manager.get("library.upload_max_mb", config_manager.get("ledger.upload_max_mb", _UPLOAD_MAX_BYTES // (1024 * 1024)))
    ) * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise FileTooLargeError(limit=max_bytes, actual=file.size)


class BookImportRequest(BaseModel):
    title: str = Field(..., description="书籍名称")
    content: str = Field(..., description="书籍完整文本内容")


class KnowledgeMetricsEvidence(BaseModel):
    document_id: str
    chapter: str = ""
    location: str = ""
    quote: str = ""


class KnowledgeMetricsRequest(BaseModel):
    """V0.2 量化拆书：对一条候选知识计算确定性量化指标（规则结果，非 LLM 裁决）。"""

    claim: str = Field(..., min_length=1)
    evidence: list[KnowledgeMetricsEvidence] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)


@router.post("/knowledge/metrics", summary="计算候选知识确定性量化指标")
async def compute_knowledge_metrics_api(payload: KnowledgeMetricsRequest) -> dict[str, Any]:
    """方向报告「三.2 量化阶段」：第一版只做确定性指标。

    纯规则计算（证据数/来源覆盖率/重复度/冲突数/条件完整度/反例/可执行步骤/相似度），
    不调用 LLM，不写入正式技能库；结果与原始候选一并作为“辅助判断”保存。
    """
    from services.knowledge_metrics import CandidateKnowledge, EvidenceRef, compute_knowledge_metrics

    candidate = CandidateKnowledge(
        claim=payload.claim,
        evidence=[EvidenceRef(**e.model_dump()) for e in payload.evidence],
        conditions=payload.conditions,
        counterexamples=payload.counterexamples,
        steps=payload.steps,
    )
    metrics = compute_knowledge_metrics(candidate)
    # 保留原始候选与规则结果，交由上层评估，而非直接当作绝对真理
    return ok(
        {"metrics": metrics, "candidate": candidate.model_dump(mode="json")},
        message="候选知识确定性量化指标计算完成（规则结果，非 LLM 裁决）",
    )


class KnowledgeClaimSubmission(BaseModel):
    """V0.2：把一条带证据的候选知识送入去重/冲突/证据门（入库）。"""

    claim: str = Field(..., min_length=1)
    evidence: list[KnowledgeMetricsEvidence] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    duplicate_threshold: float = Field(default=0.8, ge=0.0, le=1.0)


@router.post("/knowledge/claims", summary="提交候选知识（去重/冲突/证据门入库）")
async def submit_knowledge_claim(payload: KnowledgeClaimSubmission) -> dict[str, Any]:
    """报告「三.1/三.2」：无证据不入正式；确定性指标做去重/冲突门。"""
    from services.knowledge_claim_store import KnowledgeClaimStore
    from services.knowledge_metrics import CandidateKnowledge, EvidenceRef

    candidate = CandidateKnowledge(
        claim=payload.claim,
        evidence=[EvidenceRef(**e.model_dump()) for e in payload.evidence],
        conditions=payload.conditions,
        counterexamples=payload.counterexamples,
        steps=payload.steps,
    )
    outcome = await KnowledgeClaimStore().add_candidate(
        candidate, duplicate_threshold=payload.duplicate_threshold
    )
    return ok(
        {
            "action": outcome.action,
            "candidate_id": outcome.candidate_id,
            "existing_id": outcome.existing_id,
            "metrics": outcome.metrics,
        },
        message="候选知识入库门判定完成",
    )


@router.get("/knowledge/claims", summary="列出已入库候选知识（可回放可解释）")
async def list_knowledge_claims() -> dict[str, Any]:
    from services.knowledge_claim_store import KnowledgeClaimStore

    claims = await KnowledgeClaimStore().list_claims()
    return ok({"claims": claims, "count": len(claims)}, message="已入库候选知识已返回")


class KnowledgeRefineRequest(BaseModel):
    """V0.2 确定性主链路：原文段落 → 观点清单 → 证据绑定/量化/去重/冲突门 → 技能候选。

    Batch 2：支持可选 run_id/parser/model 作为 provenance（量化运行标识/解析器/模型），
    落库并随 /knowledge/claims 可查询。
    """

    document_id: str = Field(..., min_length=1)
    blocks: list[str] = Field(..., min_length=1)
    claims: list[Any] = Field(..., min_length=1)
    skill_name: str = "skill.extracted"
    run_id: str = ""
    parser: str = ""
    model: str = ""


def _upsert_reflection_session(
    *,
    run_id: str,
    source_snapshot: str,
    parser: str = "",
    model: str = "",
    status: str = "COMPLETED",
    artifact: dict[str, Any] | None = None,
) -> Any:
    """Batch 2：一次量化运行产生唯一 ReflectionSession（以 run+source 去重，幂等）。

    理由（满足约束6）：复用知识炼制的文件态事实源（same dir 语义），不新增第二套
    DB/知识事实源；以确定性 key（run_id|source|artifact 指纹）保证"同一次运行只产出
    一个会话快照"，重复调用返回既有 session，便于回放。
    返回 models.reflection.ReflectionSession 实例。
    """
    from models.reflection import ReflectionSession

    key_raw = f"{run_id or ''}\u0001{source_snapshot or ''}\u0001{json.dumps(artifact or {}, sort_keys=True)}"
    key_hash = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:16]
    session_id = f"rflx_{key_hash}"
    base = get_app_data_dir() / "reflection_sessions"
    base.mkdir(parents=True, exist_ok=True)
    session = ReflectionSession(
        session_id=session_id,
        trigger_type="AUTO",
        status=status,
        run_id=run_id,
        source_snapshot=source_snapshot,
        parser=parser,
        model=model,
        artifact=artifact or {},
        replay=False,
    )
    # 幂等：已存在同 key 会话则直接返回（不重复创建/不改字段），否则落盘。
    path = base / f"{session_id}.json"
    try:
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            return ReflectionSession(**existing)
        path.write_text(json.dumps(session.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        logger.warning("[Refine] ReflectionSession 落盘失败，仍返回内存会话 %s", session_id)
    return session


@router.post("/knowledge/refine", summary="跑一遍知识炼制主链路（确定性，非 LLM 裁决）")
async def refine_claims(payload: KnowledgeRefineRequest) -> dict[str, Any]:
    from services.knowledge_refinery import refine
    from services.skill_governance import SkillGovernance

    # 有证据且冒烟过测的主张 → 唯一注册入口 SkillGovernance.submit_candidate
    governance = SkillGovernance()
    report = await refine(
        payload.blocks,
        payload.claims,
        document_id=payload.document_id,
        skill_name=payload.skill_name,
        run_id=payload.run_id,
        parser=payload.parser,
        model=payload.model,
        governance=governance,
    )
    # Batch 2 闭环：一次量化运行产生唯一 ReflectionSession（以 run_id+source 去重），
    # 关联本次 claims/evidence/metrics 汇总，供反思入口消费（不重复创建）。
    session = _upsert_reflection_session(
        run_id=payload.run_id,
        source_snapshot=payload.document_id,
        parser=payload.parser,
        model=payload.model,
        status=report.status,
        artifact={
            "total": report.total,
            "stored": report.stored,
            "duplicates": report.duplicates,
            "conflicts": report.conflicts,
            "no_evidence": report.no_evidence,
            "proposed_candidates": report.proposed_candidates,
            "document_id": payload.document_id,
        },
    )
    return ok(
        {
            "total": report.total,
            "stored": report.stored,
            "duplicates": report.duplicates,
            "conflicts": report.conflicts,
            "no_evidence": report.no_evidence,
            "proposed_candidates": report.proposed_candidates,
            "status": report.status,
            "session_id": session.session_id,
            "run_id": payload.run_id,
            "details": [
                {
                    "claim": d.claim,
                    "action": d.action,
                    "candidate_id": d.candidate_id,
                    "existing_id": d.existing_id,
                    "skill_candidate": d.skill_candidate,
                    "evidence_count": d.evidence_count,
                    "tests_passed": d.tests_passed,
                    "governance_candidate_id": d.governance_candidate_id,
                }
                for d in report.details
            ],
        },
        message="知识炼制主链路执行完成（规则结果，非 LLM 裁决）",
    )


@router.post("/knowledge/sync", summary="把已入库量化主张同步进权威索引库/Ledger（幂等）")
async def sync_knowledge_claims(indexer: CardIndexer = Depends(get_indexer)) -> dict[str, Any]:
    """V0.2 落库打通：把 `KnowledgeClaimStore` 中已入库（带证据）的量化主张
    投影进索引库（cards + FTS + Ledger claims/evidence）并补写量化 `ledger_metrics`
    与 `CLAIM_QUANTIFIED` outbox 审计。无证据主张跳过（fail-closed），可重复调用。
    """
    from services.knowledge_claim_projection import sync_claims_to_indexer
    from services.knowledge_claim_store import KnowledgeClaimStore

    result = await sync_claims_to_indexer(KnowledgeClaimStore(), indexer)
    return ok(result, message="量化主张已同步进权威索引库/Ledger（幂等）")


@router.get("/knowledge/corpus", summary="已入库量化主张的技能语料摘要")
async def knowledge_skill_corpus() -> dict[str, Any]:
    """V0.2 技能语料打通：把已入库主张输出为技能语料摘要
    （claim/证据原文/适用条件/步骤/rule_confidence），供反思与技能炼制作为语料来源。
    """
    from services.knowledge_claim_projection import claims_to_skill_corpus
    from services.knowledge_claim_store import KnowledgeClaimStore

    entries = await claims_to_skill_corpus(KnowledgeClaimStore())
    return ok({"entries": entries, "count": len(entries)}, message="技能语料摘要已返回")


@router.post("/books/import", summary="导入新书")
async def import_book(
    req: BookImportRequest,
    ledger: LedgerRepository = Depends(get_ledger_repository),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """将新书导入系统并生成唯一 book_id，存储原始文件。"""
    # 校验输入长度，防止超大文件导致磁盘耗尽
    if len(req.content) > 50 * 1024 * 1024:  # 50MB 上限
        raise HTTPException(status_code=413, detail="书籍内容过大，最大支持 50MB")
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="书籍名称不能为空")

    book_id = f"book_{uuid.uuid4().hex[:8]}"

    # 复用项目目录规范
    books_dir = get_app_data_dir().parent / "library" / "books"
    books_dir.mkdir(parents=True, exist_ok=True)

    file_path = books_dir / f"{book_id}.txt"
    try:
        file_path.write_text(req.content, encoding="utf-8")
        passage_rows = [
            {"text": text, "char_start": offset, "char_end": offset + len(text)}
            for offset, text in _split_text_passages(req.content)
        ]
        ledger_result = await ledger.register_document(
            document_id=book_id,
            title=req.title.strip(),
            source_uri=str(file_path),
            media_type="text/plain",
            content=req.content,
            parser_id="builtin.txt",
            parser_version="1.0",
            passages=passage_rows,
        )
        _write_book_metadata(books_dir, book_id, {
            "title": req.title.strip(),
            "source_filename": f"{req.title.strip()}.txt",
            "parser_id": "builtin.txt",
            "parser_version": "1.0",
            "content_hash": hashlib.sha256(req.content.encode("utf-8")).hexdigest(),
            "passage_count": len(req.content.split("\n\n")),
        })
        logger.info("导入书籍成功: %s (User: %s)", book_id, user)
        return ok({"book_id": book_id, "ledger": ledger_result}, message="书籍导入成功")
    except Exception as e:
        logger.error("导入书籍失败: %s", e)
        raise HTTPException(status_code=500, detail="保存书籍文件失败")


@router.post("/books/upload_file", summary="上传书籍文件")
async def upload_book_file(
    file: UploadFile = File(...),
    ledger: LedgerRepository = Depends(get_ledger_repository),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """通过 Multipart/form-data 上传书籍文件，流式读取解析，规避 50MB Body 限制。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 安全加固：扩展名白名单 + 大小上限
    _validate_upload(file)

    try:
        parser = ParserFactory.get_parser(file.filename)
        # size=None（分块传输）时 _validate_upload 无法拦截大小，此处按字节上限硬读兜底
        max_bytes = int(
            config_manager.get("library.upload_max_mb", config_manager.get("ledger.upload_max_mb", 20))
        ) * 1024 * 1024
        import io as _io
        raw = file.file.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise FileTooLargeError(limit=max_bytes, actual=len(raw))
        # 方向报告 P1-2：解析总超时保护（在异步 worker 线程中执行，避免阻塞事件循环）。
        parse_timeout = load_parser_limits().timeout_seconds
        try:
            parsed_document = await asyncio.wait_for(
                asyncio.to_thread(parser.parse_document, _io.BytesIO(raw), file.filename),
                timeout=parse_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=408,
                detail=f"文档解析超时（超过 {parse_timeout}s），请确认文件未损坏或过大",
            ) from exc
        content = parsed_document.text
        if not content.strip():
            raise ValueError("文档未解析出可用文本")
    except ParserLimitError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except FileTooLargeError:
        raise
    except Exception as e:
        logger.error("解析上传文件失败: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    book_id = f"book_{uuid.uuid4().hex[:8]}"
    books_dir = get_app_data_dir().parent / "library" / "books"
    books_dir.mkdir(parents=True, exist_ok=True)

    file_path = books_dir / f"{book_id}.txt"
    try:
        file_path.write_text(content, encoding="utf-8")
        ledger_result = await ledger.register_document(
            document_id=book_id,
            title=Path(file.filename).stem,
            source_uri=str(file_path),
            media_type=parsed_document.media_type,
            content=content,
            parser_id=parsed_document.parser_id,
            parser_version=parsed_document.parser_version,
            passages=[passage.model_dump(mode="json") for passage in parsed_document.passages],
        )
        _write_book_metadata(books_dir, book_id, {
            "title": Path(file.filename).stem,
            "source_filename": file.filename,
            "parser_id": parsed_document.parser_id,
            "parser_version": parsed_document.parser_version,
            "media_type": parsed_document.media_type,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "passage_count": len(parsed_document.passages),
            "passages": [passage.model_dump(mode="json") for passage in parsed_document.passages],
        })
        logger.info("上传书籍成功: %s (User: %s)", book_id, user)
        return ok({"book_id": book_id, "passage_count": len(parsed_document.passages), "ledger": ledger_result}, message="书籍上传并解析成功")
    except Exception as e:
        logger.error("保存上传书籍文件失败: %s", e)
        raise HTTPException(status_code=500, detail="保存书籍文件失败")


@router.get("/books", summary="获取书库列表")
async def list_books(
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """读取本地存储的书籍文件并返回列表（仅返回元数据，绝对不读取和返回原文）。"""
    books_dir = get_app_data_dir().parent / "library" / "books"
    if not books_dir.exists():
        return ok([])

    meta_file = books_dir / "books_metadata.json"
    metadata = {}
    if meta_file.exists():
        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    books = []
    try:
        for file_path in books_dir.glob("*.txt"):
            if file_path.is_file():
                book_id = file_path.stem
                book_meta = metadata.get(book_id, {})
                books.append({
                    "book_id": book_id,
                    "title": book_meta.get("title") or file_path.name,
                    "size": file_path.stat().st_size,
                    "categories": book_meta.get("categories", []),
                    "sub_folders": book_meta.get("sub_folders", []),
                    "updated_at": book_meta.get("updated_at", file_path.stat().st_mtime),
                })
        # 按修改时间降序
        books.sort(key=lambda x: x["updated_at"], reverse=True)
        return ok(books)
    except Exception as e:
        logger.error("获取书库列表失败: %s", e)
        raise HTTPException(status_code=500, detail="获取书库列表失败")


class BookRenameRequest(BaseModel):
    title: str = Field(..., description="新书名")


@router.put("/books/{book_id}/rename", summary="重命名书籍")
async def rename_book(
    book_id: str,
    req: BookRenameRequest,
    ledger: LedgerRepository = Depends(get_ledger_repository),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """重命名书库中的书籍（更新元数据中的 title）。"""
    books_dir = get_app_data_dir().parent / "library" / "books"
    file_path = books_dir / f"{book_id}.txt"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="书籍不存在")
    meta_file = books_dir / "books_metadata.json"
    metadata = {}
    if meta_file.exists():
        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    if book_id not in metadata:
        metadata[book_id] = {}
    metadata[book_id]["title"] = req.title
    metadata[book_id]["updated_at"] = __import__("time").time()
    meta_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    document = await ledger.read_document(book_id)
    if document is None:
        raise HTTPException(status_code=409, detail="Ledger 文档不存在，拒绝只修改旧 metadata")
    await ledger.upsert_document(__import__("models.ledger", fromlist=["DocumentRecord"]).DocumentRecord(**{**document, "title": req.title, "updated_at": __import__("services.ledger_repository", fromlist=["_now"])._now()}))
    await ledger.enqueue(book_id, "DOCUMENT_RENAMED", {"document_id": book_id, "title": req.title})
    await ledger.indexer.conn.commit()
    return ok(message="书籍已重命名")


@router.delete("/books/{book_id}", summary="删除书籍")
async def delete_book(
    book_id: str,
    ledger: LedgerRepository = Depends(get_ledger_repository),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """删除书库中的书籍文件与元数据。"""
    books_dir = get_app_data_dir().parent / "library" / "books"
    file_path = books_dir / f"{book_id}.txt"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="书籍不存在")
    try:
        if not await ledger.archive_document(book_id, reason="book_deleted"):
            raise HTTPException(status_code=404, detail="Ledger 文档不存在")
        file_path.unlink()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("删除书籍文件或 Ledger 文档失败: %s", e)
        raise HTTPException(status_code=500, detail="删除书籍文件失败")
    meta_file = books_dir / "books_metadata.json"
    if meta_file.exists():
        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
            metadata.pop(book_id, None)
            meta_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return ok(message="书籍已删除")


class QuantizeRequest(BaseModel):
    mode: str = Field(default="both", description="量化模式: info, data, both")
    model: str | None = Field(default=None, description="指定量化使用的模型")
    extraction: str | None = Field(default=None, description="提取模式: llm/rule/auto")
    quantize_round: int = Field(default=1, description="量化轮次: 1=粗扫 2=深挖 3=精炼")


@router.post("/books/{book_id}/quantize", summary="启动书籍量化任务")
async def quantize_book(
    book_id: str,
    req: QuantizeRequest,
    quantifier: BookQuantifier = Depends(get_quantifier),
    db: DatabaseManager = Depends(get_db),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """触发指定书籍的异步量化任务，分解章节并提取卡片。"""
    # 校验量化模式
    if req.mode not in ("info", "data", "both"):
        raise HTTPException(status_code=400, detail="无效的量化模式，可选: info, data, both")

    # 重复量化与并发管控
    queue_msg = "量化任务已启动"
    try:
        # 1. 重复量化防护：检查当前书籍是否有正在运行的量化任务
        # 转义 LIKE 通配符（% / _），防止 book_id 含通配符导致误匹配其它任务
        escaped_book = book_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cursor = await db.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE raw_command LIKE ? ESCAPE '\\' "
            "AND status IN ('PENDING', 'RUNNING')",
            (f"quantize book {escaped_book}%",)
        )
        row = await cursor.fetchone()
        if row and row[0] > 0:
            raise HTTPException(status_code=409, detail=f"书籍 {book_id} 正在进行量化任务，请勿重复提交")

        # 2. 并发粒度管控：检查全局运行中的量化任务数量
        max_concurrent = config_manager.get("library.max_concurrent_quantize", 2)
        cursor_total = await db.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE raw_command LIKE 'quantize book %' AND status IN ('PENDING', 'RUNNING')"
        )
        total_row = await cursor_total.fetchone()
        if total_row and total_row[0] >= max_concurrent:
            queue_msg = f"当前已有 {total_row[0]} 个量化任务在运行/排队，您的任务已加入队列等待执行。"

    except HTTPException:
        raise
    except Exception as e:
        logger.error("检查量化任务状态失败: %s", e)
        # 不阻断提交流程，仅记录错误
        queue_msg = "量化任务已提交"
    try:
        task_id = await quantifier.submit_quantize_task(
            book_id, req.mode, model=req.model, extraction=req.extraction,
            quantize_round=req.quantize_round
        )
        return ok({"task_id": task_id}, message=queue_msg)
    except Exception as e:
        logger.error("启动量化任务失败: %s", e)
        # Batch 7：不向客户端泄漏 str(exception)，返回统一安全错误码
        from core.errors import http_error
        raise http_error(500, "STRATEGY_QUANTIZE_SUBMIT_FAILED")


@router.get("/cards/search", summary="查询卡片(多维检索)")
async def search_cards(
    card_type: str | None = None,
    subtype: str | None = None,
    keyword: str | None = None,
    source_book: str | None = None,
    limit: int = 50,
    offset: int = 0,
    min_utility: float | None = None,
    min_entropy: float | None = None,
    indexer: CardIndexer = Depends(get_indexer),
    ledger_facade=Depends(get_ledger_read_facade),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """兼容返回结构；read_mode=ledger 时事实查询来自 Ledger。

    min_utility / min_entropy 为可选的「四象限」阈值过滤：返回结果按
    utility_score / entropy_score（>= 阈值）过滤，缺省不约束。
    """
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    try:
        if await ledger_facade.is_readable():
            results = await ledger_facade.search_cards(keyword=keyword, source_book=source_book, limit=limit, offset=offset)
            total = await ledger_facade.count_cards(keyword=keyword, source_book=source_book)
        else:
            results = await indexer.search_cards(card_type=card_type, subtype=subtype, keyword=keyword, source_book=source_book, limit=limit, offset=offset)
            total = await indexer.count_search_cards(card_type=card_type, category=None, subtype=subtype, keyword=keyword, source_book=source_book)

        # 四象限阈值过滤（可选；对查询结果做内存过滤，保持分页语义为「过滤后」的结果）
        results = _filter_cards_by_quadrant(results, min_utility, min_entropy)

        return ok({"results": results, "total": total})
    except Exception as e:
        logger.error("检索卡片失败: %s", e)
        raise HTTPException(status_code=500, detail="数据库查询失败")


@router.get("/cards/{card_id}/detail", summary="读取卡片详情")
async def get_card_detail(
    card_id: str,
    indexer: CardIndexer = Depends(get_indexer),
    ledger_facade=Depends(get_ledger_read_facade),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """加载卡片详情。authoritative/read_mode=ledger 时从 ledger_claims 读取。"""
    if await ledger_facade.is_readable():
        detail = await ledger_facade.get_card_detail(card_id)
    else:
        detail = await indexer.get_card_detail(card_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"卡片不存在: {card_id}")
    return ok(detail)


@router.delete("/cards/{card_id}", summary="删除卡片")
async def delete_card(
    card_id: str,
    indexer: CardIndexer = Depends(get_indexer),
    ledger_facade=Depends(get_ledger_read_facade),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """删除指定卡片。authoritative 下归档 ledger claim（cards 表为空投影时不再误报 404）。"""
    try:
        if await ledger_facade.is_readable():
            detail = await ledger_facade.get_card_detail(card_id)
            is_system = detail and any(t.startswith("auto_") for t in (detail.get("tags") or []))
            success = await ledger_facade.delete_card(card_id)
        else:
            detail = await indexer.get_card_detail(card_id)
            is_system = detail and any(t.startswith("auto_") for t in (detail.get("tags") or []))
            success = await indexer.delete_card(card_id)
        if not success:
            raise HTTPException(status_code=404, detail="卡片不存在")
        if is_system:
            return ok(message="系统产物卡片已归档（可在 archive 中恢复）")
        return ok(message="卡片已删除")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("删除卡片失败: %s", e)
        raise HTTPException(status_code=500, detail="删除卡片失败")


@router.put("/cards/{card_id}", summary="[Agent Tool] 更新/合并卡片内容")
async def update_card(
    card_id: str,
    payload: dict[str, Any],
    indexer: CardIndexer = Depends(get_indexer),
    ledger_facade=Depends(get_ledger_read_facade),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """
    提供给智能体的活水知识库接口：
    读取已有卡片，并将传入的增量 payload 进行合并，然后调用 save_card 覆盖保存。
    从而实现旧知识的动态演进。
    """
    from models.cards import InfoCard, DataCard

    if await ledger_facade.is_readable():
        old_data = await ledger_facade.get_card_detail(card_id)
        # Ledger claim 无 card_type 字段，按 info 处理（与 facade.knowledge_type 语义一致）
        if old_data is not None and not old_data.get("card_type"):
            old_data["card_type"] = "info"
    else:
        old_data = await indexer.get_card_detail(card_id)
    if not old_data:
        raise HTTPException(status_code=404, detail="无法更新不存在的卡片")

    # 合并增量数据 (简单浅层合并，也可扩展为深层)
    for k, v in payload.items():
        old_data[k] = v

    # 重建模型以触发生效校验
    try:
        if old_data.get("card_type") == "info":
            card = InfoCard(**old_data)
        elif old_data.get("card_type") == "data":
            card = DataCard(**old_data)
        elif old_data.get("card_type") == "extend":
            from models.cards import ExtendCard
            card = ExtendCard(**old_data)
        else:
            from models.cards import BaseCard
            card = BaseCard(**old_data)  # type: ignore
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"卡片数据格式验证失败: {e}")

    await indexer.save_card(card)
    return ok(message="卡片已动态更新")


@router.get("/books/{book_id}/info-cards", summary="获取书籍资料总管理面板数据")
async def get_book_info_cards(
    book_id: str,
    min_utility: float | None = None,
    min_entropy: float | None = None,
    indexer: CardIndexer = Depends(get_indexer),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """按分类聚合返回书籍的资料卡片（世界观、剧情、人设、风格）。"""
    try:
        # 修复：min_utility/min_entropy 为 None 时不应参与过滤。
        # 原实现直接透传 None，SQL 层 `utility_score >= NULL` 恒为假，
        # 导致四象限筛选开启后结果永远为空。
        results = await indexer.search_cards(
            card_type="info",
            source_book=book_id,
            min_utility=(min_utility if min_utility is not None else None),
            min_entropy=(min_entropy if min_entropy is not None else None),
            limit=500,
        )
        # 按 category 分组
        grouped = {
            "worldview": [],
            "plot": [],
            "character": [],
            "style": [],
            "misc": [],
        }
        for card in results:
            cat = card.get("category", "misc")
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append(card)

        return ok(grouped)
    except Exception as e:
        logger.error("获取书籍资料卡片失败: %s", e)
        raise HTTPException(status_code=500, detail="获取资料卡片失败")


@router.get("/books/{book_id}/data-cards", summary="获取书籍数据总管理面板数据")
async def get_book_data_cards(
    book_id: str,
    min_utility: float | None = None,
    min_entropy: float | None = None,
    indexer: CardIndexer = Depends(get_indexer),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """返回书籍的数据量化卡片。"""
    try:
        # 修复：同上，None 过滤条件不传，避免 SQL `>= NULL` 恒假。
        results = await indexer.search_cards(
            card_type="data",
            source_book=book_id,
            min_utility=(min_utility if min_utility is not None else None),
            min_entropy=(min_entropy if min_entropy is not None else None),
            limit=500,
        )
        return ok(results)
    except Exception as e:
        logger.error("获取书籍数据卡片失败: %s", e)
        raise HTTPException(status_code=500, detail="获取数据卡片失败")


@router.get("/tasks/{task_id}/status", summary="查询任务进度")
async def get_task_status(
    task_id: str,
    db: DatabaseManager = Depends(get_db),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """查询批次1任务状态及所属分段 (Segment) 执行情况。"""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    segments = await db.get_segments_for_task(task_id)
    total = len(segments)
    completed = sum(1 for s in segments if s["status"] == "COMPLETED")
    failed = sum(1 for s in segments if s["status"] == "FAILED")

    return ok({
        "task_id": task_id,
        "status": task["status"],
        "progress": {
            "total_segments": total,
            "completed": completed,
            "failed": failed,
            "percent": round(completed / total * 100, 2) if total > 0 else 0,
        },
        "error_message": task.get("error_message"),
    })
