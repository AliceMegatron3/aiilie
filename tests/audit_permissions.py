"""
tests/audit_permissions.py
审计沙盒：测试智能体的权限边界，验证其是否会出现多抓、少抓、越权等情况。
"""
import sys
import os
import asyncio
import logging

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.dispatcher import ModelDispatcher
from strategies.llm_extraction import LLMExtractionStrategy
from services.card_registry import CardTypeRegistry

# 极简配置日志
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

async def run_audit():
    logger.info("=== 启动智能体权限边界审计沙盒 ===")
    
    # 依赖注入，构造假壳子以最小化依赖
    class DummyTaskManager: pass
    class DummyProjectManager:
        async def get_project(self, project_id): return None
    class DummyIndexer: pass

    dispatcher = ModelDispatcher(
        task_manager=DummyTaskManager(),
        indexer=DummyIndexer(),
        project_manager=DummyProjectManager(),
        local_model="qwen2.5:7b" # 指定本地 Qwen 测试
    )
    
    registry = CardTypeRegistry()
    strategy = LLMExtractionStrategy(registry, dispatcher)
    
    # 极端混合测试样本
    test_text = (
        "王腾站在灵台山之上，手握上古法宝天帝圣剑，眼神冷漠。"
        "突然，他怒吼一声，全身罡气爆发，直接施展出绝技‘天帝碎灭斩’，"
        "一剑将前方的山峰劈成两半，恐怖的冲击波将叶凡震退了十里。"
        "躲在远处的姬紫月看到这一幕，心如刀绞，眼泪忍不住流了下来，她恨自己没有能力去帮忙。"
    )
    
    context = {"book_id": "audit_test", "chapter": "ch_01"}
    
    logger.info("【输入测试文段】:\n" + test_text + "\n")
    logger.info("正在调用三大专员同时对本段进行权限分离提取...")
    
    cards = await strategy.extract(test_text, context)
    
    logger.info("=== 提取结果分析 ===")
    for card in cards:
        logger.info(f"卡片类型: {card.__class__.__name__} | Tag: {card.tags}")
        logger.info(f"提取内容: {card.content}")
        logger.info("-" * 40)
        
    if not cards:
        logger.warning("大模型未提取到任何数据，可能是本地模型未启动或响应超时。")

if __name__ == "__main__":
    asyncio.run(run_audit())
