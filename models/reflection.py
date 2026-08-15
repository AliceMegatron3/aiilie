"""
models/reflection.py — 思考与反思系统数据模型
===================================================
基于 Pydantic 的反思会话、优化规则与通用技能沉淀数据结构。
用于记录批次4的自我进化与调度策略沉淀结果。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def generate_uuid() -> str:
    """生成默认 UUID。"""
    return str(uuid4())


def current_utc_time() -> str:
    """获取当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


class ReflectionSession(BaseModel):
    """单次反思/自学习任务的执行记录"""
    session_id: str = Field(default_factory=generate_uuid, description="反思会话唯一ID")
    trigger_type: Literal["MANUAL", "AUTO", "EVENT"] = Field(
        default="MANUAL", 
        description="触发方式"
    )
    start_time: str = Field(default_factory=current_utc_time, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    status: Literal["RUNNING", "COMPLETED", "FAILED"] = Field(
        default="RUNNING", 
        description="会话当前状态"
    )
    report_path: str | None = Field(
        default=None, 
        description="本地化存储的完整反思报告 (JSON) 路径"
    )


class OptimizationRule(BaseModel):
    """系统级自适应优化规则"""
    rule_id: str = Field(default_factory=generate_uuid, description="规则唯一ID")
    scope: Literal["TASK_SPLIT", "MODEL_DISPATCH", "CARD_EXTRACTION", "NOVEL_AGENT_SCHEDULE"] = Field(
        ..., 
        description="规则适用的作用域（第十部分新增 NOVEL_AGENT_SCHEDULE：多智能体调度）"
    )
    condition: dict[str, Any] = Field(
        default_factory=dict, 
        description="JSON格式的触发条件 (如 {'task_type': 'creation', 'estimated_length': {'>': 3000}})"
    )
    action: dict[str, Any] = Field(
        default_factory=dict, 
        description="JSON格式的执行动作 (如 {'set_compute_mode': 'think'})"
    )
    confidence: float = Field(
        default=0.0, 
        ge=0.0, 
        le=1.0, 
        description="规则置信度 (0~1)，低于阈值不自动生效"
    )
    is_active: bool = Field(default=True, description="是否处于激活状态")
    created_at: str = Field(default_factory=current_utc_time, description="创建时间")


class UniversalSkill(BaseModel):
    """从高频优质数据中沉淀的通用技能/模板卡"""
    skill_id: str = Field(default_factory=generate_uuid, description="技能唯一ID")
    type: Literal["TEMPLATE", "PATTERN", "STYLE"] = Field(
        ..., 
        description="技能类别：结构模板/创作模式/叙事风格"
    )
    name: str = Field(..., description="技能名称，如 '人物设定黄金四要素'")
    content: dict[str, Any] = Field(
        default_factory=dict, 
        description="JSON格式的技能内容与结构约束"
    )
    source_cards: list[str] = Field(
        default_factory=list, 
        description="提取此技能来源的高频引用的原书库卡片 ID 列表"
    )
    applicability: str = Field(default="", description="该技能适用场景描述，用于 AI 上下文匹配")
    created_at: str = Field(default_factory=current_utc_time, description="沉淀时间")
