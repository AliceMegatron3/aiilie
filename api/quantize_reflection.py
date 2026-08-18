from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import logging

from services.indexer import CardIndexer
from api.library import get_indexer
from api.deps import get_ledger_repository, verify_token
from services.ledger_repository import LedgerRepository
from core.config_manager import config_manager
from services.experience_manager import experience_manager
from services.scratchpad_manager import scratchpad_manager
from core.response import ok
from models.cards import DataCard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["Quantization & Reflection"], dependencies=[Depends(verify_token)])

class ReflectionPayload(BaseModel):
    experience_content: str
    cards_data: List[dict] # { "metric_type": "emotion_curve", "value": {...} }

@router.post("/books/{book_id}/quantize/converge", summary="[Agent Tool] 提交跨章量化统筹结果与反思")
async def converge_scratchpad_and_reflect(
    book_id: str,
    payload: ReflectionPayload,
    indexer: CardIndexer = Depends(get_indexer),
    ledger: LedgerRepository = Depends(get_ledger_repository),) -> dict[str, Any]:
    """
    量化智能体调用此接口：
    1. 提交根据 scratchpad 分析出的宏观图表数据 (cards_data)，系统将其生成为 DataCard
    2. 提交对于原作者写作技法的反思顿悟 (experience_content)
    3. 清理该书籍对应的临时沙盒
    """
    try:
        # 1. 批量生成 DataCard
        saved_cards = []
        cards_to_save = []
        for card_input in payload.cards_data:
            metric_type = card_input.get("metric_type")
            value = card_input.get("value")
            if not metric_type or not value:
                continue
            
            card = DataCard(
                source_book_id=book_id,
                content=f"由跨章量化自动统筹生成的 {metric_type} 数据",
                metric_type=metric_type,
                value=value,
                entropy_score=0.2, # 系统生成的结构化数据默认低信息熵
                utility_score=0.9  # 高有用度
            )
            cards_to_save.append(card)
            saved_cards.append(card.card_id)
            
        if not config_manager.get_bool("ledger.authoritative", False):
            await indexer.save_cards(cards_to_save)

        # 跨章统筹是派生指标，不伪造原文 source_anchor；Ledger 保留 draft artifact。
        for card in cards_to_save:
            await ledger.register_derived_artifact(
                artifact_id=f"derived_{card.card_id}",
                source_scope=book_id,
                artifact_type="quantize_convergence_metric",
                payload={"metric_type": card.metric_type, "value": card.value, "card_id": card.card_id},
                source_document_id=book_id,
                source_run_id="quantize-converge",
            )

        # 2. 提交反思经验
        exp = experience_manager.add_experience(
            exp_type="creative", 
            content=f"[量化反思: {book_id}] " + payload.experience_content
        )

        # 3. 销毁临时沙盒
        await scratchpad_manager.destroy_scratchpad(book_id)

        return ok(
            {
                "saved_cards_count": len(saved_cards),
                "experience_id": exp["id"]
            },
            message="数据量化收敛与反思提交成功",
        )
    except Exception as e:
        logger.error("收敛反思失败: %s", e)
        raise HTTPException(status_code=500, detail="收敛草稿与生成反思失败")

@router.post("/books/{book_id}/scratchpad", summary="[Agent Tool] 写入或更新草稿区")
async def update_book_scratchpad(
    book_id: str,
    updates: dict[str, Any]
) -> dict[str, Any]:
    """供智能体在量化书籍的逐章循环中，记录全书的增量数据（如练气期铺垫字数等）。"""
    try:
        new_data = await scratchpad_manager.update_scratchpad(book_id, updates)
        return ok(new_data, message="草稿区已更新")
    except Exception as e:
        logger.error("更新 Scratchpad 失败: %s", e)
        raise HTTPException(status_code=500, detail="更新 Scratchpad 失败")
