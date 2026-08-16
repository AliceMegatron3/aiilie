"""
models/cards.py — 知识库卡片数据模型
=======================================
基于 Pydantic 的双层卡片体系（资料卡与数据卡），用于书籍量化和分层索引。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def generate_uuid() -> str:
    """生成默认 UUID。"""
    return str(uuid4())


def current_utc_time() -> str:
    """获取当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


class SourceAnchor(BaseModel):
    """A stable, human-auditable location inside a source document."""

    source_document_id: str = ""
    source_path: str = ""
    heading: str = ""
    paragraph: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    quote: str = ""


class CardRelation(BaseModel):
    """A typed edge between two cards."""

    relation_id: str = Field(default_factory=generate_uuid)
    source_card_id: str = ""
    target_card_id: str
    relation_type: str = "related_to"
    weight: float = 1.0
    note: str = ""
    status: str = "active"


class BaseCard(BaseModel):
    """所有卡片的基类"""
    card_id: str = Field(default_factory=generate_uuid, description="卡片唯一标识")
    source_book_id: str = Field(..., description="来源书籍的唯一标识")
    source_chapter: str = Field(default="", description="来源章节或具体位置")
    content: str = Field(..., description="卡片的核心文本内容或结构化摘要")
    tags: list[str] = Field(default_factory=list, description="标签集合")
    create_time: str = Field(default_factory=current_utc_time, description="创建时间")
    
    # 四象限分析用打分 (默认值 0.5 为中等)
    entropy_score: float = Field(default=0.5, description="冷门度/信息熵，越高代表越罕见。")

    # The following fields are optional in spirit and have defaults so cards
    # created by earlier versions remain valid when read from cold storage.
    library_id: str = Field(default="", description="所属资料库标识")
    knowledge_type: str = Field(default="FACT", description="语义类型：FACT/RULE/ENTITY/STATE/TEMPLATE/RELATION")
    book_type_ids: list[str] = Field(default_factory=list, description="可复用的书籍类型/题材标识")
    domain: str = Field(default="", description="知识域，如 history、worldview、style")
    scope_level: str = Field(default="book", description="作用域：global/library/book/project/session")
    status: str = Field(default="draft", description="审核状态：draft/reviewed/approved/rejected/archived")
    evidence_level: str = Field(default="unknown", description="证据等级：A/B/C/D/unknown")
    rule_strength: str = Field(default="none", description="规则强度：none/advisory/hard")
    source_document_id: str = Field(default="", description="稳定的来源文档标识")
    source_anchor: SourceAnchor | None = Field(default=None, description="来源段落锚点")
    valid_time_start: str | None = Field(default=None, description="约束生效时间起点")
    valid_time_end: str | None = Field(default=None, description="约束生效时间终点")
    valid_places: list[str] = Field(default_factory=list, description="约束适用地点")
    related_card_ids: list[str] = Field(default_factory=list, description="关联卡片标识")
    relations: list[CardRelation] = Field(default_factory=list, description="卡片关系边")
    utility_score: float = Field(default=0.5, description="有用度/核心度，越高代表越核心。")


class InfoCard(BaseCard):
    """资料索引卡：用于存储世界观、人设、设定等文本型提取资料"""
    card_type: str = Field(default="info", description="大类：info")
    category: str = Field(
        default="misc", 
        description="四大基础分区：worldview(世界观), plot(剧情), character(人设), style(风格), misc(其他)"
    )
    card_sub_type: str = Field(..., description="子类型，如：世界设定、人物性格")
    original_fragment: str = Field(default="", description="引用的原文片段，用于追溯")
    payload: dict[str, Any] = Field(default_factory=dict, description="自由负载数据结构，供智能体高阶动态知识管理使用")


class DataCard(BaseCard):
    """数据量化卡：用于存储节奏曲线、冲突密度等量化型数据"""
    card_type: str = Field(default="data", description="大类：data")
    metric_type: str = Field(
        ..., 
        description="指标类型，如：emotion_curve(情绪曲线), progression_pacing(成长节奏), conflict_frequency(冲突密度), relationship_topology(人际拓扑)"
    )
    value: dict[str, Any] | float | int | str = Field(
        ..., 
        description="量化数值或结构化JSON数据。例如：{'练气期_chapters': 50, '筑基期_chapters': 120}"
    )


class ExtendCard(BaseCard):
    """扩展接口卡：为未来功能（如通用技能库）预留的基类"""
    card_type: str = Field(default="extend", description="大类：extend")
    extension_module: str = Field(..., description="负责处理该卡的扩展模块名称")
    payload: dict[str, Any] = Field(default_factory=dict, description="自定义扩展数据")
