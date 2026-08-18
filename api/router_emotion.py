from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from core.database import DatabaseManager
from api.deps import get_db, get_llm_client, verify_token
from services.emotion_engine.frame_manager import EmotionFrameManager
from services.emotion_engine.quantifier_service import EmotionQuantifier
from core.config_manager import config_manager
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emotion", tags=["Emotion Engine"], dependencies=[Depends(verify_token)])

class QuantifyRequest(BaseModel):
    text: str
    project_id: str | None = None
    book_id: str | None = None

class GenerateRequest(BaseModel):
    frame_id: str


class DesireStateRequest(BaseModel):
    scene_text: str
    character_profile: dict = Field(default_factory=dict)
    previous_state: dict = Field(default_factory=dict)
    related_assets: list = Field(default_factory=list)


class AssetExtractRequest(BaseModel):
    source_text: str
    project_id: str = ""
    book_id: str = ""
    task_id: str = ""
    chapter_index: str = ""
    scene_id: str = ""
    source_file: str = ""
    source_hash: str = ""
    paragraph_range: str = ""
    deterministic_metrics: dict = Field(default_factory=dict)
    context_assets: list = Field(default_factory=list)

def get_engine_services(db: DatabaseManager, request: Request = None):
    if not config_manager.get("feature.emotion_quantify_enable", False):
        raise HTTPException(status_code=403, detail="Emotion Engine is disabled.")

    # 复用 app.state（bootstrap 已初始化），避免新建未初始化的连接
    frame_manager = EmotionFrameManager(db)
    batch1 = getattr(request.app.state, "batch1_task_manager", None) if request else None
    indexer = getattr(request.app.state, "indexer", None) if request else None
    quantifier = EmotionQuantifier(frame_manager, batch1_task_manager=batch1, indexer=indexer)
    return frame_manager, quantifier

@router.post("/quantify")
async def quantify_text(
    req: QuantifyRequest,
    request: Request,
    db: DatabaseManager = Depends(get_db),
    llm_client=Depends(get_llm_client),
):
    """单段文本实时情感量化（真实 LLM；云端未启用返回 503，不再返回 MOCK）。"""
    if llm_client is None:
        raise HTTPException(
            status_code=503,
            detail="云端大模型未启用，无法执行情感量化。请先在设置中配置 API 密钥并开启开关。",
        )
    frame_manager, quantifier = get_engine_services(db, request)
    try:
        frame = await quantifier.quantify_single_fragment(
            req.text, req.project_id, req.book_id, llm_client=llm_client
        )
        return {"status": "success", "frame": frame.model_dump(mode="json")}
    except Exception as e:
        logger.error("情感量化失败: %s", e)
        raise HTTPException(status_code=502, detail=f"情感量化失败: {e}")


@router.post("/quantify/desire-state")
async def quantify_desire_state(
    req: DesireStateRequest,
    request: Request,
    db: DatabaseManager = Depends(get_db),
    llm_client=Depends(get_llm_client),
):
    """生成证据约束的情感—欲望临时状态卡。"""
    _, quantifier = get_engine_services(db, request)
    try:
        result = await quantifier.quantify_desire_state(
            req.scene_text,
            character_profile=req.character_profile,
            previous_state=req.previous_state,
            related_assets=req.related_assets,
            llm_client=llm_client,
        )
        return {"status": "success", "state_card": result}
    except Exception as e:
        logger.error("情感欲望状态卡生成失败: %s", e)
        raise HTTPException(status_code=502, detail=f"情感欲望状态卡生成失败: {e}")


@router.post("/quantify/assets")
async def extract_quantization_assets(
    req: AssetExtractRequest,
    db: DatabaseManager = Depends(get_db),
    llm_client=Depends(get_llm_client),
):
    """生成分层量化资产草稿；结果必须经过后续审核才能成为正式资产。"""
    _, quantifier = get_engine_services(db)
    try:
        result = await quantifier.extract_quantization_assets(
            req.source_text,
            project_id=req.project_id,
            book_id=req.book_id,
            task_id=req.task_id,
            chapter_index=req.chapter_index,
            scene_id=req.scene_id,
            source_file=req.source_file,
            source_hash=req.source_hash,
            paragraph_range=req.paragraph_range,
            deterministic_metrics=req.deterministic_metrics,
            context_assets=req.context_assets,
            llm_client=llm_client,
        )
        return {"status": "success", "assets": result}
    except Exception as e:
        logger.error("分层量化资产抽取失败: %s", e)
        raise HTTPException(status_code=502, detail=f"分层量化资产抽取失败: {e}")

@router.post("/generate")
async def generate_text(
    req: GenerateRequest,
    db: DatabaseManager = Depends(get_db),
    llm_client=Depends(get_llm_client),
):
    """根据情感payload生成小说片段（接入真实 LLM，未启用云端时 503）。"""
    frame_manager, quantifier = get_engine_services(db)
    frame = await frame_manager.load_frame_full(req.frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found.")
        
    context_frames = []
    if frame.source_project_id:
        context_frames = await frame_manager.get_recent_standard_frames(frame.source_project_id, 3)
        
    prompt = quantifier.render_generation_prompt(frame, context_frames)

    if llm_client is None:
        raise HTTPException(
            status_code=503,
            detail="云端大模型未启用，请先在设置中配置 API 密钥并开启开关。",
        )

    generated = await llm_client.generate_completion(prompt, temperature=0.9, max_tokens=2048)
    return {"status": "success", "rendered_prompt": prompt, "generated_text": generated}

@router.post("/book/{book_id}/quantize-task")
async def batch_quantize_book(
    book_id: str,
    request: Request,
    db: DatabaseManager = Depends(get_db),
):
    """启动整本书籍批量情感量化任务（批次1 CommandTask：章节分片 + 断点续算）。"""
    frame_manager, quantifier = get_engine_services(db, request)

    # 读取书籍原文：分片交给批次1 CommandSplitter（by_chapter）
    from core.path_resolver import safe_join, get_app_data_dir

    books_dir = get_app_data_dir().parent / "library" / "books"
    book_file = safe_join(books_dir, f"{book_id}.txt")
    if book_file.exists():
        raw_text = book_file.read_text(encoding="utf-8")
    else:
        raise HTTPException(status_code=404, detail=f"书籍文件不存在: {book_id}")

    try:
        task = await request.app.state.batch1_task_manager.submit_task(
            raw_command=f"emotion quantize book {book_id}\n{raw_text}",
            priority=5,
            segment_strategy="by_chapter",
        )
        return {"status": "success", "task_id": task.task_id}
    except Exception as e:
        logger.error("批量情感量化任务投递失败: %s", e)
        from core.errors import http_error
        raise http_error(500, "EMOTION_BATCH_DISPATCH_FAILED")

@router.get("/frames")
async def list_frames(project_id: str = None, limit: int = 50, db: DatabaseManager = Depends(get_db)):
    """获取帧列表（分页，只返回元数据）"""
    frame_manager, quantifier = get_engine_services(db)
    # 限制 limit 范围，防止超大查询
    limit = min(max(limit, 1), 200)
    query = "SELECT frame_id, frame_type, wave_level, mode, create_time FROM emotion_frames"
    params = []
    if project_id:
        query += " WHERE source_project_id = ?"
        params.append(project_id)
        
    query += " ORDER BY create_time DESC LIMIT ?"
    params.append(limit)
    
    cursor = await db.conn.execute(query, tuple(params))
    rows = await cursor.fetchall()
    
    frames = [
        {"frame_id": r[0], "frame_type": r[1], "wave_level": r[2], "mode": r[3], "create_time": r[4]}
        for r in rows
    ]
    return {"status": "success", "data": frames}

@router.get("/frames/{frame_id}")
async def get_frame_detail(frame_id: str, db: DatabaseManager = Depends(get_db)):
    """获取单帧完整详情（读取磁盘文件）"""
    frame_manager, quantifier = get_engine_services(db)
    
    frame = await frame_manager.load_frame_full(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame disk file or meta not found.")
        
    return {"status": "success", "data": frame.model_dump(mode="json")}
