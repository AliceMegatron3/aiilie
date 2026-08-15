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
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from api.deps import get_db, get_indexer, get_quantifier, verify_token
from core.config_manager import config_manager
from core.database import DatabaseManager
from core.exceptions import (
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from core.path_resolver import get_app_data_dir
from core.response import ok
from services.indexer import CardIndexer
from services.quantifier import BookQuantifier
from services.parser import ParserFactory

logger = logging.getLogger(__name__)

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
        config_manager.get("library.upload_max_mb", _UPLOAD_MAX_BYTES // (1024 * 1024))
    ) * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise FileTooLargeError(limit=max_bytes, actual=file.size)


class BookImportRequest(BaseModel):
    title: str = Field(..., description="书籍名称")
    content: str = Field(..., description="书籍完整文本内容")


@router.post("/books/import", summary="导入新书")
async def import_book(
    req: BookImportRequest,
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
        logger.info("导入书籍成功: %s (User: %s)", book_id, user)
        return ok({"book_id": book_id}, message="书籍导入成功")
    except Exception as e:
        logger.error("导入书籍失败: %s", e)
        raise HTTPException(status_code=500, detail="保存书籍文件失败")


@router.post("/books/upload_file", summary="上传书籍文件")
async def upload_book_file(
    file: UploadFile = File(...),
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
            config_manager.get("library.upload_max_mb", 20)
        ) * 1024 * 1024
        import io as _io
        raw = file.file.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise FileTooLargeError(limit=max_bytes, actual=len(raw))
        content = parser.parse(_io.BytesIO(raw), file.filename)
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
        logger.info("上传书籍成功: %s (User: %s)", book_id, user)
        return ok({"book_id": book_id}, message="书籍上传并解析成功")
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
                    "title": file_path.name,
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


class QuantizeRequest(BaseModel):
    mode: str = Field(default="both", description="量化模式: info, data, both")


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
        task_id = await quantifier.submit_quantize_task(book_id, req.mode)
        return ok({"task_id": task_id}, message=queue_msg)
    except Exception as e:
        logger.error("启动量化任务失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cards/search", summary="查询卡片(多维检索)")
async def search_cards(
    subtype: str | None = None,
    keyword: str | None = None,
    source_book: str | None = None,
    limit: int = 50,
    offset: int = 0,
    indexer: CardIndexer = Depends(get_indexer),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """根据类型、关键字、书籍来源在热索引 SQLite 中高速检索卡片元数据。"""
    # 限制查询参数范围，防止 DoS
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    try:
        results = await indexer.search_cards(
            subtype=subtype,
            keyword=keyword,
            source_book=source_book,
            limit=limit,
            offset=offset,
        )
        # 修复：total 应为命中总数（分页前），而非本页返回条数。
        # 先执行与 search_cards 相同的条件过滤 COUNT，保证前端分页正确。
        total = await indexer.count_search_cards(
            card_type=None,
            category=None,
            subtype=subtype,
            keyword=keyword,
            source_book=source_book,
        )
        return ok({"results": results, "total": total})
    except Exception as e:
        logger.error("检索卡片失败: %s", e)
        raise HTTPException(status_code=500, detail="数据库查询失败")


@router.get("/cards/{card_id}/detail", summary="读取卡片详情")
async def get_card_detail(
    card_id: str,
    indexer: CardIndexer = Depends(get_indexer),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """加载冷存储 JSON 文件，返回完整卡片内容。"""
    detail = await indexer.get_card_detail(card_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"卡片不存在: {card_id}")
    return ok(detail)


@router.put("/cards/{card_id}", summary="[Agent Tool] 更新/合并卡片内容")
async def update_card(
    card_id: str,
    payload: dict[str, Any],
    indexer: CardIndexer = Depends(get_indexer),
    user: str = Depends(verify_token),
) -> dict[str, Any]:
    """
    提供给智能体的活水知识库接口：
    读取已有卡片，并将传入的增量 payload 进行合并，然后调用 save_card 覆盖保存。
    从而实现旧知识的动态演进。
    """
    from models.cards import InfoCard, DataCard

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
