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
        
        # 规则策略只能保存有证据的结果；没有注册处理器时返回空集，
        # 不再生成“默认规则摘要”或虚构 metric_score。
        for definition in self.registry.get_all_types():
            subtype = definition.subtype
            processor = self.registry.get_processor(subtype)
            if processor is None:
                continue
            try:
                result = processor(text, context)
                if hasattr(result, "__await__"):
                    result = await result
                if not isinstance(result, list):
                    continue
                for card in result:
                    if isinstance(card, BaseCard):
                        cards.append(card)
            except Exception as e:
                logger.warning("规则处理器 %s 执行失败: %s", subtype, e)
                
        logger.info("DefaultStrategy 提取完成，共生成 %d 张卡片", len(cards))
        return cards


class FallbackStrategy(ExtractionStrategy):
    """
    LLM 优先 + 规则兜底的组合策略。
    - primary: 主提取策略（通常是 LLMExtractionStrategy）
    - fallback: 兜底策略（通常是 DefaultStrategy）
    - fallback_on_failure: 主策略抛异常或产出 0 张卡时自动回退兜底。
    """
    def __init__(
        self,
        registry: CardTypeRegistry,
        primary: ExtractionStrategy,
        fallback: ExtractionStrategy,
        fallback_on_failure: bool = True,
    ) -> None:
        super().__init__(registry)
        self.primary = primary
        self.fallback = fallback
        self.fallback_on_failure = fallback_on_failure

    async def extract(self, text: str, context: dict[str, Any]) -> list[BaseCard]:
        try:
            cards = await self.primary.extract(text, context)
            if cards:
                return cards
            # 主策略产出了空结果（例如模型认为该段无职责内容）
            if self.fallback_on_failure:
                logger.info("[FallbackStrategy] 主策略未产出卡片，回退规则兜底")
                return await self.fallback.extract(text, context)
            return cards
        except Exception as e:
            logger.warning("[FallbackStrategy] 主策略异常，回退规则兜底: %s", e)
            return await self.fallback.extract(text, context)
