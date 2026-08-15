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
