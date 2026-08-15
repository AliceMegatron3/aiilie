"""
models/novel_agent.py — 多智能体小说创作 自学习闭环 数据模型 (第十部分)
======================================================================
1. NovelAgentExecutionAudit：每次多 Agent 任务执行的审计记录
   - 记录子 Agent 调用、token 耗时、命中分数、OOC 告警、熔断标记
   - 记录用到的 skill_id / rule_id
   - 支持用户外部反馈打分与 OOC 标记
2. NovelAgentSkill：结构化技能包 (继承 UniversalSkill)
   - 子 Agent 开关、模板覆盖、RAG 过滤条件、触发条件、历史效果统计
   - 以批次2 InfoCard 保存 (card_sub_type="novel_agent_skill")
纯叠加式设计：不修改批次4核心模型，仅新增本文件。
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field
from models.reflection import UniversalSkill
def generate_uuid() -> str:
    """生成默认 UUID。"""
    return str(uuid4())
def current_utc_time() -> str:
    """获取当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()
# ============================================================
# 子 Agent 调用记录
# ============================================================
class SubAgentCallRecord(BaseModel):
    """单次子 Agent 调用的审计记录。"""
    agent_role: str = Field(..., description="子Agent角色标识，如 lore_expert / combat_expert")
    is_active: bool = Field(default=True, description="该子Agent本次是否激活")
    model_assigned: str = Field(default="", description="分配使用的模型")
    calls: int = Field(default=0, ge=0, description="调用次数")
    total_tokens: int = Field(default=0, ge=0, description="累计消耗 token")
    duration_ms: int = Field(default=0, ge=0, description="累计耗时(毫秒)")
    hit_score: float = Field(default=0.0, ge=0.0, le=1.0, description="命中分数(0~1)")
    ooc_warnings: int = Field(default=0, ge=0, description="OOC(角色失控)告警次数")
    error_count: int = Field(default=0, ge=0, description="失败/异常次数")
# ============================================================
# 多 Agent 执行审计（用户任务级）
# ============================================================
class NovelAgentExecutionAudit(BaseModel):
    """
    每次多 Agent 创作任务的完整审计记录。
    - task_id: 关联的创作任务 ID (也可以是 create_xxx 闭包任务)
    - user_feedback_score: 用户外部反馈打分 (0~5，None 表示未反馈)
    - user_ooc_marked: 用户是否手动标记 OOC
    - is_circuit_break: 本次任务是否被熔断保护
    """
    audit_id: str = Field(default_factory=generate_uuid, description="审计记录唯一ID")
    task_id: str = Field(..., description="关联的创作任务 ID")
    project_id: str = Field(default="", description="项目 ID")
    genre: str = Field(default="", description="创作题材，用于技能检索")
    scenario: str = Field(default="", description="创作场景，用于技能检索")
    agent_calls: list[SubAgentCallRecord] = Field(
        default_factory=list, description="子Agent调用记录列表"
    )
    total_tokens: int = Field(default=0, ge=0, description="总 token 消耗")
    total_duration_ms: int = Field(default=0, ge=0, description="总耗时(毫秒)")
    hit_score: float = Field(default=0.0, ge=0.0, le=1.0, description="整体命中分数")
    ooc_warnings: int = Field(default=0, ge=0, description="OOC 告警总数")
    circuit_break: bool = Field(default=False, description="本次任务是否触发熔断")
    circuit_break_reason: str = Field(default="", description="熔断原因")
    used_rule_ids: list[str] = Field(default_factory=list, description="本次任务使用的调度规则ID列表")
    used_skill_ids: list[str] = Field(default_factory=list, description="本次任务使用的技能ID列表")
    user_feedback_score: float | None = Field(
        default=None, ge=0.0, le=5.0, description="用户外部反馈打分(0~5)"
    )
    user_ooc_marked: bool = Field(default=False, description="用户是否手动标记 OOC")
    user_comment: str = Field(default="", description="用户补充说明")
    created_at: str = Field(default_factory=current_utc_time, description="审计记录创建时间")
    updated_at: str = Field(default_factory=current_utc_time, description="最近更新时间")
# ============================================================
# NovelAgentSkill — 创作智能体结构化技能包
# ============================================================
class NovelAgentSkill(UniversalSkill):
    """
    结构化技能包，继承批次4 UniversalSkill。
    扩展字段：
    - agent_overrides: 子Agent开关覆盖 (role -> {enabled, model, priority})
    - template_overrides: 模板覆盖 (template_id -> template_text)
    - rag_filter: RAG 检索过滤条件
    - trigger_conditions: 触发条件 (题材/场景/关键词)
    - effect_stats: 历史效果统计
    - status: ACTIVE / ARCHIVED / PENDING_REVIEW
    """
    type: Literal["TEMPLATE", "PATTERN", "STYLE"] = "PATTERN"
    card_sub_type: str = "novel_agent_skill"
    agent_overrides: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="子Agent开关覆盖，如 {'combat_expert': {'enabled': False, 'model': 'qwen2.5:7b'}}",
    )
    template_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="模板覆盖 (template_id -> 模板文本)",
    )
    rag_filter: dict[str, Any] = Field(
        default_factory=dict,
        description="RAG 检索过滤条件，如 {'category': 'worldview', 'min_utility': 0.6}",
    )
    trigger_conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="触发条件，如 {'genre': '玄幻', 'scenario': '战斗'}",
    )
    # 历史效果统计
    effect_stats: dict[str, Any] = Field(
        default_factory=lambda: {
            "apply_count": 0,
            "hit_sum": 0.0,
            "avg_hit_score": 0.0,
            "total_tokens": 0,
            "ooc_sum": 0,
            "circuit_break_count": 0,
            "user_feedback_count": 0,
            "user_feedback_sum": 0.0,
            "effect_score": 0.0,
        },
        description="历史效果统计",
    )
    status: Literal["ACTIVE", "ARCHIVED", "PENDING_REVIEW"] = Field(
        default="ACTIVE", description="技能状态：生效/归档/待人工审核"
    )
    archived_reason: str = Field(default="", description="归档原因")
    created_at: str = Field(default_factory=current_utc_time, description="沉淀时间")