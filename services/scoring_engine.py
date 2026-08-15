"""
services/scoring_engine.py — 生成后打分引擎 (Phase 9)
===================================================
在文章生成完成后，由“纪检委”子智能体对大模型的输出进行交叉审核，
判断是否遵循了书库卡片的要求，并将有价值的经验（打分较低时）沉淀到全局经验库。
"""
import asyncio
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

class ScoringEngine:
    """生成后打分与经验沉淀引擎"""
    
    def __init__(self, reflection_engine=None):
        # 依赖注入经验反思引擎，用于将打分结果写入经验库
        self.reflection_engine = reflection_engine

    async def score_generation(
        self, 
        project_id: str, 
        command_text: str, 
        generated_content: str, 
        used_cards: list[dict] = None
    ) -> dict:
        """
        核心打分逻辑：
        模拟让子智能体去审查刚刚生成的文本，看是否契合了参考的卡片。
        """
        logger.info("[ScoringEngine] 正在启动生成后交叉打分审查... (Project: %s)", project_id)
        
        # 这里在实际生产中应该请求大模型，传入 command, generated_content, 还有参考的卡片
        # 让它输出一个包含 {"score": 85, "improvement": "..."} 的 JSON
        # 此处我们通过启发式逻辑模拟大模型的审查过程
        
        score = 100
        improvements = []
        
        # 启发式 1: 如果指令里提到了战斗，但生成内容里动词密度极低（简单模拟）
        if "战斗" in command_text or "杀" in command_text:
            if "剑" not in generated_content and "拳" not in generated_content and len(generated_content) > 100:
                score -= 30
                improvements.append("战力评估师反馈：战斗描写缺乏动作细节与功法招式的展现，像是在回合制念诗。")
                
        # 启发式 2: 长文本逻辑一致性惩罚（模拟）
        if len(generated_content) > 800 and "？" not in generated_content and "！" not in generated_content:
            score -= 15
            improvements.append("情感导师反馈：长篇幅描写中缺乏情感波动和标点符号的情绪传递。")
            
        # 启发式 3: 有卡片但未引用
        if used_cards and len(used_cards) > 0:
            # 简单模拟，实际上应检查关键词
            score -= 10
            improvements.append("设定纪检委反馈：似乎没有完全使用书库中提供的世界观卡片。")

        # 随机波动增加真实感
        import random
        score -= random.randint(0, 10)
        score = max(0, score)

        logger.info("[ScoringEngine] 审查完毕。最终得分: %d", score)

        result = {
            "score": score,
            "improvements": improvements
        }
        
        # 如果得分低于 80 分，将建议写入经验库
        if score < 80:
            logger.info("[ScoringEngine] 分数低于预期，将建议沉淀为全局经验法则！")
            for imp in improvements:
                try:
                    # 将经验沉淀到全局项目（或者特定项目）。这里按照架构，注入为经验规则
                    await self._record_experience(imp)
                except Exception as e:
                    logger.error("[ScoringEngine] 沉淀经验失败: %s", e)
                    
        return result

    async def _record_experience(self, improvement: str):
        """将失败教训总结为全局提示词锁定规则"""
        # 调用批次 4 的 reflection_engine 或 optimization_applier
        # 实际代码中调用 self.reflection_engine 暴露的接口
        # 这里打桩日志
        logger.info("[ScoringEngine] (Stub) 成功向经验库追加新纪律: %s", improvement)

# 全局单例
scoring_engine = ScoringEngine()
