"""
models/task.py — 核心 Pydantic 数据模型
=============================================
定义 CommandTask（命令任务）和 Segment（执行分段）两大核心模型。
所有字段均使用 Pydantic V2 语法进行数据验证。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── 分段策略字面量（补丁：短指令高负载强制分段） ─────────────────

# 合法 segment_strategy 取值集合。
# 说明：该字段为字符串存储（无需数据库迁移），历史存量任务全部为 "auto"。
SEGMENT_STRATEGY_ALLOWED: frozenset[str] = frozenset(
    {"auto", "force_split", "no_split", "by_length", "by_chapter"}
)


def normalize_segment_strategy(value: Any) -> str:
    """
    补丁：segment_strategy 值归一化 + 非法值兜底回退 "auto"。

    - None / 非法字符串 / 未知策略名 → "auto"（保证存量与脏参数不崩溃）；
    - 大小写与首尾空白容忍（"FORCE_SPLIT" → "force_split"）。
    """
    if value is None:
        return "auto"
    s = str(value).strip().lower()
    return s if s in SEGMENT_STRATEGY_ALLOWED else "auto"


# ── 枚举类型 ──────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    """任务生命周期状态。"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SegmentStatus(str, Enum):
    """分段执行状态。"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ModelSource(str, Enum):
    """
    模型来源枚举 — 批次3将实现具体调用逻辑。
    本批次仅做标准化路由标记。
    """
    LOCAL = "local"       # 本地模型（如 Ollama）
    CLOUD = "cloud"       # 云端 API（如 OpenAI 兼容接口）
    HYBRID = "hybrid"     # 混合模式：优先本地，失败回退云端


# ── 辅助函数 ──────────────────────────────────────────────────────

def _gen_id(prefix: str = "") -> str:
    """生成带可选前缀的唯一标识符。"""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 核心模型 ──────────────────────────────────────────────────────

class Segment(BaseModel):
    """
    执行分段模型。
    
    每个 Segment 是 CommandTask 拆解后的原子执行单元。
    tail_context 字段在分段间传递"思维链"或"中间变量"，
    确保长任务的连贯性。
    """
    segment_id: str = Field(default_factory=lambda: _gen_id("seg_"))
    parent_task_id: str = Field(..., description="所属任务 ID")
    content_payload: str = Field(..., description="该段需处理的具体内容")
    sequence_order: int = Field(..., ge=0, description="执行顺序（从0开始）")
    status: SegmentStatus = Field(default=SegmentStatus.PENDING)
    tail_context: dict[str, Any] = Field(
        default_factory=dict,
        description="上一段执行遗留的尾巴数据（JSON），用于传递思维链或中间变量",
    )
    prev_tail: str | None = Field(default=None, description="前一片末尾 50 字符")
    next_head: str | None = Field(default=None, description="后一片开头 50 字符")
    # 批次7：深度思考阶段标记（Optional，普通任务为 None）
    phase: str | None = Field(
        default=None,
        description="思考阶段：MAPPING/SCANNING/ANALYZING/VALIDATING/CONCLUDING（批次7 深度思考）",
    )
    result_content: str | None = Field(default=None, description="执行结果内容")
    output_path: str | None = Field(
        default=None, description="输出文件路径（pathlib.Path 序列化为字符串）"
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class BasePipelineTask(BaseModel):
    """所有后台任务的调度基类"""
    task_id: str = Field(default_factory=lambda: _gen_id("task_"))
    task_type: str = Field(default="command", description="任务类型标识")
    priority: int = Field(default=4, ge=1, le=7, description="优先级 1(最高)-7(最低)")
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    error_message: str | None = None
    retry_count: int = Field(default=0)
    
    # 为了兼容部分旧代码使用 dict 取值的习惯，增加配置
    model_config = {"extra": "allow"}

class CommandTask(BasePipelineTask):
    """
    命令任务模型。
    代表用户提交的一条完整指令，可被 CommandSplitter
    拆解为多个 Segment 进行分段执行。
    """
    task_type: str = "command"
    raw_command: str = Field(default="", description="用户原始指令")
    segment_strategy: str = Field(
        default="auto",
        description="分段策略名称。非法值经校验自动回退 auto。",
    )
    model_source: ModelSource = Field(
        default=ModelSource.LOCAL,
        description="模型来源：local/cloud/hybrid",
    )
    # 架构整改 1.1：平行宇宙分支隔离。None 表示未绑定分支（默认时间线）。
    branch_id: str | None = Field(
        default=None,
        description="任务绑定的文档分支ID（tail 上下文分支隔离用）",
    )
    segments: list[Segment] = Field(default_factory=list)

    @field_validator("segment_strategy", mode="before")
    @classmethod
    def _coerce_segment_strategy(cls, v: Any) -> str:
        """补丁：Pydantic 校验层兜底，非法值回退 auto，保证存量任务兼容。"""
        return normalize_segment_strategy(v)

class LearningTask(BasePipelineTask):
    task_type: str = "learning"
    doc_id: str
    project_id: str

class QuantizeTask(BasePipelineTask):
    task_type: str = "quantize"
    book_id: str
    raw_command: str = ""

class ReflectionTask(BasePipelineTask):
    task_type: str = "reflection"
    session_id: str | None = None


# ── API 请求/响应模型 ────────────────────────────────────────────

class TaskSubmitRequest(BaseModel):
    """提交任务的 API 请求体。"""
    raw_command: str = Field(..., min_length=1, description="用户原始指令")
    priority: int = Field(default=4, ge=1, le=7)
    segment_strategy: str = Field(default="auto")
    model_source: ModelSource = Field(default=ModelSource.LOCAL)

    @field_validator("segment_strategy", mode="before")
    @classmethod
    def _coerce_segment_strategy(cls, v: Any) -> str:
        """补丁：API 入参同样兜底，非法值回退 auto。"""
        return normalize_segment_strategy(v)


class TaskStatusResponse(BaseModel):
    """任务状态查询的 API 响应体。"""
    task_id: str
    status: TaskStatus
    priority: int
    segment_count: int
    completed_segments: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None


class TaskAuditReport(BaseModel):
    """任务审计报告。"""
    task_id: str
    total_segments: int
    completed_segments: int
    failed_segments: int
    success_rate: float
    total_duration_seconds: float | None
    average_segment_duration_seconds: float | None
    status: TaskStatus
