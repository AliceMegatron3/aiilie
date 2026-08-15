"""
models/project.py — 创作者项目管理与自学习数据模型
===================================================
基于 Pydantic 的项目、文档生命周期管理及 AI 自学习解析结果定义。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from models.timeline import Timeline


def generate_uuid() -> str:
    """生成默认 UUID。"""
    return str(uuid4())


def current_utc_time() -> str:
    """获取当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


class AuthorProject(BaseModel):
    """作者创作项目模型"""
    project_id: str = Field(default_factory=generate_uuid, description="项目全局唯一ID")
    project_name: str = Field(..., description="项目/小说名称")
    genre: str = Field(default="unknown", description="主要题材类型")
    style_tags: list[str] = Field(default_factory=list, description="风格标签列表，如 '悬疑', '快节奏'")
    bind_book_ids: list[str] = Field(
        default_factory=list, 
        description="关联的书库书籍ID列表（批次2联动）"
    )
    default_compute_mode: Literal["rapid", "think"] = Field(
        default="rapid", 
        description="默认算力与模型调度模式"
    )
    # ── 业务补丁扩展（第五部分，全部 Optional，向后兼容） ──
    # 补丁1：非线性叙事时间轴
    timelines: list[Timeline] = Field(
        default_factory=list,
        description="项目时间线列表（补丁1 非线性叙事时间轴系统）",
    )
    # 补丁2：角色语调 TTS（角色名 → 音色标识）
    character_voice_profiles: dict[str, str] = Field(
        default_factory=dict,
        description="角色音色映射（补丁2 沉浸式角色语调 TTS），如 {'主角': 'zh-CN-XiaoxiaoNeural'}",
    )
    created_at: str = Field(default_factory=current_utc_time, description="创建时间")
    updated_at: str = Field(default_factory=current_utc_time, description="最后更新时间")


class ProjectDoc(BaseModel):
    """项目内部的创作文档模型"""
    doc_id: str = Field(default_factory=generate_uuid, description="文档唯一ID")
    project_id: str = Field(..., description="所属项目的ID")
    doc_name: str = Field(..., description="文档名称，如 '第一章' 或 '大纲'")
    raw_content: str = Field(default="", description="文档原始文本内容")
    status: Literal["DRAFT", "PUBLISHED", "ARCHIVED"] = Field(
        default="DRAFT", 
        description="文档状态"
    )
    ai_parse_path: str | None = Field(
        default=None, 
        description="该文档对应的 AI 学习结果文件路径 (ai_parse.json)"
    )
    # 架构整改 1.1：当前活跃分支（平行宇宙时间线）。None 视为默认分支 "main"。
    current_branch_id: str | None = Field(
        default=None,
        description="当前活跃分支ID（补丁3分支版本系统），None 等价默认分支 main",
    )
    created_at: str = Field(default_factory=current_utc_time, description="创建时间")
    updated_at: str = Field(default_factory=current_utc_time, description="最后更新时间")


class AIParseResult(BaseModel):
    """文档 AI 自学习结果结构化模型"""
    doc_id: str = Field(..., description="被分析的文档ID")
    project_id: str = Field(..., description="所属项目的ID")
    genre_inferred: str = Field(default="", description="推断的题材")
    style_analysis: str = Field(default="", description="风格深层分析摘要")
    key_characters: list[dict[str, str]] = Field(
        default_factory=list, 
        description="识别的角色及其简介列表 [{'name': '张三', 'desc': '主角...'}]"
    )
    core_demands: list[str] = Field(
        default_factory=list, 
        description="提炼的核心创作意图与需求点"
    )
    index_keywords: list[str] = Field(
        default_factory=list, 
        description="可用于检索的全文高亮关键词索引"
    )
    analyzed_at: str = Field(default_factory=current_utc_time, description="分析完成时间")
