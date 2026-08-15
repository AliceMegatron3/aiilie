from typing import Any
from pydantic import BaseModel, Field
from enum import Enum

class SubAgentRole(str, Enum):
    LORE_EXPERT = "lore_expert"       # 设定纪检委（生成 Info Card）
    COMBAT_EXPERT = "combat_expert"   # 战力评估师（生成 Data Card 战斗部分）
    EMOTION_EXPERT = "emotion_expert" # 情感导师（生成 Data Card 情感部分）
    EVENT_EXPERT = "event_expert"     # 事件简报专员（一句话大纲）

class TaskExecutionNode(BaseModel):
    role: SubAgentRole
    is_active: bool = Field(description="是否激活该专员")
    reason: str = Field(description="总督决定激活或挂起该专员的理由")
    model_assigned: str = Field(description="分配给该专员的模型名")

class ExecutionDAG(BaseModel):
    """总督调度器返回的执行图表"""
    chapter_summary_sniffed: str = Field(description="总督嗅探到的本章大致剧情")
    nodes: list[TaskExecutionNode] = Field(description="各专员的执行状态")
    
class OrchestratorRequest(BaseModel):
    book_id: str
    chapter_content: str
    primary_model: str = "deepseek-r1:7b"
    worker_model: str = "qwen2.5:7b"
