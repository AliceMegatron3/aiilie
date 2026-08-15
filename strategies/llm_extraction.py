"""
strategies/llm_extraction.py — 基于真实大模型的内容提取策略
===================================================
通过调用 ModelDispatcher 与 ExpertAgents，使用大模型并行提取各种卡片。
"""
import sys
import io
import json
import logging
import asyncio
from typing import Any

# 修复：移除模块顶层对 sys.stdout/sys.stderr 的全局替换。
# 原实现把进程级标准流包装成 TextIOWrapper，会破坏所有导入本模块的
# 宿主进程（pytest 收集、PyInstaller 打包、GUI 应用），且重复导入时
# 对已包装流二次包装会抛错。UTF-8 支持应由应用层/解释器配置解决。

from models.cards import BaseCard, DataCard, InfoCard
from models.orchestrator_schemas import SubAgentRole
from services.card_registry import CardTypeRegistry
from services.dispatcher import ModelDispatcher
from services.expert_agents import expert_agents
from strategies.extraction import ExtractionStrategy

logger = logging.getLogger(__name__)

class LLMExtractionStrategy(ExtractionStrategy):
    def __init__(self, registry: CardTypeRegistry, dispatcher: ModelDispatcher) -> None:
        super().__init__(registry)
        self.dispatcher = dispatcher

    async def extract(self, text: str, context: dict[str, Any]) -> list[BaseCard]:
        book_id = context.get("book_id", "unknown_book")
        chapter = context.get("chapter", "unknown_chapter")
        logger.info(f"LLMExtractionStrategy 开始对文本进行大模型推理... 目标书籍: {book_id}")

        cards: list[BaseCard] = []

        # 针对每个专职智能体，构建其Prompt并分发给模型
        tasks = [
            self._invoke_agent(SubAgentRole.LORE_EXPERT, "info", "worldview", text, book_id, chapter),
            self._invoke_agent(SubAgentRole.COMBAT_EXPERT, "data", "combat", text, book_id, chapter),
            self._invoke_agent(SubAgentRole.EMOTION_EXPERT, "data", "emotion", text, book_id, chapter),
            self._invoke_agent(SubAgentRole.EVENT_EXPERT, "info", "event", text, book_id, chapter)
        ]

        # 并行调用大模型
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, list):
                cards.extend(res)
            elif isinstance(res, Exception):
                logger.error(f"大模型提取时发生异常: {res}")

        logger.info(f"LLMExtractionStrategy 提取完成，共生成 {len(cards)} 张真实卡片。")
        return cards

    async def _invoke_agent(self, role: SubAgentRole, category: str, subtype: str, text: str, book_id: str, chapter: str) -> list[BaseCard]:
        # 1. 获取专员的系统提示词
        prompt = expert_agents.get_expert_prompt(role, text)
        prompt += "\n\n请以 JSON 格式输出，必须包含 final_data 字典。如果有提取到数据，则写入键值对；如果该段落没有任何属于你职责范围的内容，请返回空字典 {}。仅返回纯 JSON，不要有任何其他解释文字！"

        # 2. 调用大模型调度器 (强制使用 rapid 模式以测试本地模型)
        response_text = await self.dispatcher.dispatch(prompt, override_mode="rapid")

        logger.info(f"RAW OUTPUT [{role.value}]:\n{response_text}")
        
        # 3. 解析结果
        try:
            # 清理 Markdown 代码块包装
            cleaned = response_text.replace("```json", "").replace("```", "").strip()
            data = json.loads(cleaned)
            
            # 兼容处理：如果模型外层包了 final_data 就用，没包就把整个 json 当作 data
            final_data = data.get("final_data", data)
            
            # 如果没有提取到任何内容，说明触发了权限边界拦截
            if not final_data:
                logger.info(f"【{role.value}】认为该段落中没有它的职责内容，权限边界生效。")
                return []

            # 4. 根据类别组装卡片
            if category == "info":
                return [InfoCard(
                    source_book_id=book_id,
                    source_chapter=chapter,
                    content=json.dumps(final_data, ensure_ascii=False),
                    tags=[subtype, role.value],
                    card_sub_type=subtype,
                    original_fragment=text[:200]
                )]
            elif category == "data":
                return [DataCard(
                    source_book_id=book_id,
                    source_chapter=chapter,
                    content=json.dumps(final_data, ensure_ascii=False),
                    tags=[subtype, role.value],
                    metric_type=subtype,
                    value=final_data
                )]
        except json.JSONDecodeError:
            logger.warning(f"【{role.value}】返回的不是合法 JSON: {response_text[:100]}...")
            return []
            
        return []
