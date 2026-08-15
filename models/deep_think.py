"""
models/deep_think.py — 批次7：深度思考与元认知扩展系统模型
=============================================================
DeepThinkSegment / DeepThinkTail / SystemImprovementTicket（历史骨架保留）
+ DeepThinkTask（批次1任务）/ CheckPoint / ThoughtProgressEvent（批次7 新增）
"""
from pydantic import BaseModel, Field
from typing import List, Optional

# 思考五阶段（状态机）
THINK_STAGES: List[str] = ["MAPPING", "SCANNING", "ANALYZING", "VALIDATING", "CONCLUDING"]


class DeepThinkSegment(BaseModel):
    id: str
    task_id: str
    phase: str = Field(description="One of: MAPPING, SCANNING, ANALYZING, VALIDATING, CONCLUDING")
    content: str


class DeepThinkTail(BaseModel):
    current_focus: str = ""
    checked_items: List[str] = Field(default_factory=list)
    found_issues: List[str] = Field(default_factory=list)
    thought_trace: List[str] = Field(default_factory=list)


class SystemImprovementTicket(BaseModel):
    """软件进化建议书条目。"""
    issue_description: str
    affected_module: str
    suggested_action: str
    # 批次7 扩展（Optional，向后兼容）
    ticket_id: Optional[str] = None
    severity: str = "MEDIUM"  # LOW / MEDIUM / HIGH / CRITICAL
    evidence: Optional[str] = None
    created_at: Optional[str] = None


class CheckPoint(BaseModel):
    """深度思考检查点：任务中断后可从最近检查点恢复。"""
    checkpoint_id: str
    task_id: str
    stage: str  # 已完成阶段名
    stage_index: int  # 已完成阶段序号（0-based）
    snapshot: dict = Field(default_factory=dict, description="阶段产出快照")
    thought_trace: List[str] = Field(default_factory=list)
    created_at: str


class ThoughtProgressEvent(BaseModel):
    """WebSocket 深度思考实时进度事件负载。"""
    task_id: str
    stage: str
    stage_index: int
    total_stages: int = 5
    message: str
    progress_pct: float = 0.0


class DeepThinkReport(BaseModel):
    """创作长思考流水线最终分析报告。"""
    task_id: str
    title: str
    stages: List[dict] = Field(default_factory=list, description="各阶段产出摘要")
    found_issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    conclusion: str = ""
    created_at: str = ""
