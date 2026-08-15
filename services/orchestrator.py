import json
import logging
from typing import Any
from models.orchestrator_schemas import OrchestratorRequest, ExecutionDAG, TaskExecutionNode, SubAgentRole

logger = logging.getLogger(__name__)

class Dispatcher:
    """
    总督调度中心 (Orchestrator)
    负责读取内容、感知所选模型，并决定哪些专职专员被激活。
    """
    
    def _sniff_content(self, content: str) -> dict[str, Any]:
        """
        极速嗅探内容，决定专员的激活状态。
        未来可接入小模型进行真正的高级推演，目前采用关键字混合规则。
        """
        # 简单的情景嗅探，实际中应交给 worker_model (如 qwen2.5:7b)
        combat_keywords = ["剑", "击", "杀", "轰", "血", "死", "倒飞", "震", "气势"]
        emotion_keywords = ["说", "笑", "怒", "泪", "恨", "你", "我", "他", "看"]
        
        has_combat = any(k in content for k in combat_keywords)
        has_emotion = any(k in content for k in emotion_keywords)
        
        summary = "日常过渡章节"
        if has_combat and has_emotion:
            summary = "带对话的战斗冲突章节"
        elif has_combat:
            summary = "纯战斗章节"
        elif has_emotion:
            summary = "纯文戏/对话交互章节"
            
        return {
            "summary": summary,
            "has_combat": has_combat,
            "has_emotion": has_emotion
        }

    def generate_dag(self, req: OrchestratorRequest) -> ExecutionDAG:
        """根据嗅探结果和可用模型，生成算力分配与调度计划图 (DAG)"""
        
        sniff = self._sniff_content(req.chapter_content)
        nodes = []
        
        # 1. 设定纪检委 (Lore) -> 任何时候都激活，但分配给便宜快速的模型
        nodes.append(TaskExecutionNode(
            role=SubAgentRole.LORE_EXPERT,
            is_active=True,
            reason="设定提取是常态任务，始终开启。",
            model_assigned=req.worker_model
        ))
        
        # 2. 战力评估师 (Combat)
        if sniff["has_combat"]:
            nodes.append(TaskExecutionNode(
                role=SubAgentRole.COMBAT_EXPERT,
                is_active=True,
                reason="嗅探到战斗相关关键词，需要分析战斗逻辑。",
                model_assigned=req.primary_model if len(req.chapter_content) > 2000 else req.worker_model
            ))
        else:
            nodes.append(TaskExecutionNode(
                role=SubAgentRole.COMBAT_EXPERT,
                is_active=False,
                reason="未发现战斗情节，挂起以节省算力。",
                model_assigned="none"
            ))
            
        # 3. 情感导师 (Emotion)
        if sniff["has_emotion"]:
            nodes.append(TaskExecutionNode(
                role=SubAgentRole.EMOTION_EXPERT,
                is_active=True,
                reason="嗅探到人物交互，需要分析情感羁绊。",
                model_assigned=req.worker_model
            ))
        else:
            nodes.append(TaskExecutionNode(
                role=SubAgentRole.EMOTION_EXPERT,
                is_active=False,
                reason="疑似纯景物描写或修炼闭关，挂起情感导师。",
                model_assigned="none"
            ))
            
        return ExecutionDAG(
            chapter_summary_sniffed=sniff["summary"],
            nodes=nodes
        )

dispatcher = Dispatcher()
