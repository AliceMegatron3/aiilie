"""
strategies/extraction.py — 内容提取与卡片生成策略
===================================================
定义卡片信息提取的策略抽象基类，以及通用的默认提取实现。
供批次1流水线中的 Segment 执行单元调用。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from models.cards import BaseCard, DataCard, InfoCard
from services.card_registry import CardTypeRegistry

logger = logging.getLogger(__name__)


class ExtractionStrategy(ABC):
    """
    内容提取策略抽象基类。
    预留未来按体裁（如科幻、武侠）或特定需求拓展复杂NLP模型提取算法的接口。
    """
    
    def __init__(self, registry: CardTypeRegistry) -> None:
        self.registry = registry

    @abstractmethod
    async def extract(self, text: str, context: dict[str, Any]) -> list[BaseCard]:
        """
        核心提取方法。
        
        Args:
            text: 待分析提取的原始章节文本。
            context: 由 TailContextManager 传递的上下文（包含 book_id, chapter 等元信息）。
            
        Returns:
            生成的卡片对象列表（InfoCard 或 DataCard）。
        """
        pass


class DefaultStrategy(ExtractionStrategy):
    """
    基础默认提取策略实现。
    根据 CardTypeRegistry 中注册的卡片类型定义，利用简单规则生成基础卡片。
    """
    
    async def extract(self, text: str, context: dict[str, Any]) -> list[BaseCard]:
        cards: list[BaseCard] = []
        book_id = context.get("book_id", "unknown_book")
        chapter = context.get("chapter", "unknown_chapter")
        
        logger.debug("DefaultStrategy 开始提取文本，来源: %s, 章节: %s", book_id, chapter)
        
        # 遍历注册中心所有有效的卡片类型定义，尝试提取对应内容
        # 注意：此处仅做基础模拟和文本封装，后续可替换为真正的大模型 API 提取
        for definition in self.registry.get_all_types():
            subtype = definition.subtype
            category = definition.card_category
            
            # 在实际业务中，此处应有基于正则表达式或本地微调模型的匹配逻辑
            # 当前为了演示通用化接口与卡片组装，生成简单的适配结构
            summary_content = f"根据默认规则提炼的 [{subtype}] 核心摘要"
            
            try:
                if category == "info":
                    # 封装 InfoCard (资料索引卡)
                    card = InfoCard(
                        source_book_id=book_id,
                        source_chapter=chapter,
                        content=summary_content,
                        tags=[subtype, "auto_extract"],
                        card_sub_type=subtype,
                        original_fragment=text[:200] if text else ""
                    )
                    cards.append(card)
                elif category == "data":
                    # 封装 DataCard (数据量化卡)
                    card = DataCard(
                        source_book_id=book_id,
                        source_chapter=chapter,
                        content=summary_content,
                        tags=[subtype, "auto_quantify"],
                        metric_type=subtype,
                        value={"metric_score": 0.5, "reliability": "low"}
                    )
                    cards.append(card)
                else:
                    logger.warning("未知的卡片类别 [%s]，跳过生成: %s", category, subtype)
            except Exception as e:
                logger.error("生成 %s 卡片时发生异常: %s", subtype, e)
                
        logger.info("DefaultStrategy 提取完成，共生成 %d 张卡片", len(cards))
        return cards
