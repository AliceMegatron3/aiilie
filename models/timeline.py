"""
models/timeline.py — 补丁1：非线性叙事时间轴系统数据模型
=========================================================
定义 Timeline（时间线/多行轨道容器）与 TimelineEvent（时间线事件）。
时间轴用于管理非线性叙事中的平行剧情时间线与事件锚点。

设计约束（向后兼容优先）：
- 所有字段挂载在 AuthorProject.timelines（Optional，默认空列表）；
- 模型独立存放，旧代码零感知。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TimelineEvent(BaseModel):
    """时间线事件：叙事中的单一剧情节点。"""

    event_id: str = Field(default_factory=lambda: _gen_id("evt"))
    title: str = Field(..., description="事件标题，如 '主角觉醒'")
    description: str = Field(default="", description="事件描述")
    # 事件发生的叙事时间点（可选，用于时间逻辑校验）
    story_time: Optional[str] = Field(default=None, description="叙事时间点，如 '第一章' 或 '公元前300年'")
    # 轨道索引：决定事件渲染在哪一行（多行轨道）
    track_index: int = Field(default=0, ge=0, description="轨道索引（从0开始）")
    # 横向位置（0-100，时间轴上的相对位置）
    position_x: float = Field(default=0.0, ge=0.0, le=100.0, description="时间轴横向位置（0-100）")
    # 涉及的人物及状态（冲突检测用）
    character_states: dict[str, str] = Field(
        default_factory=dict,
        description="事件发生时的人物状态映射，如 {'主角': '存活', '师傅': '已死'}",
    )
    # 事件类型
    event_type: Literal["PLOT", "DEATH", "BIRTH", "MEETING", "BATTLE", "REVEAL", "OTHER"] = Field(
        default="PLOT", description="事件类型"
    )
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class Timeline(BaseModel):
    """非线性叙事时间线：一条时间线包含多个事件（多行轨道）。"""

    timeline_id: str = Field(default_factory=lambda: _gen_id("tl"))
    name: str = Field(..., description="时间线名称，如 '主线'、'回忆线'")
    description: str = Field(default="", description="时间线说明")
    # 轨道标签（多行轨道：每行一个叙事视角/角色线）
    tracks: list[str] = Field(default_factory=lambda: ["主线"], description="轨道名称列表")
    events: list[TimelineEvent] = Field(default_factory=list, description="时间线事件列表")
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class TimelineConflict(BaseModel):
    """时间线冲突报告条目。"""

    conflict_id: str = Field(default_factory=lambda: _gen_id("cf"))
    severity: Literal["ERROR", "WARNING"] = Field(default="WARNING")
    conflict_type: Literal[
        "CHARACTER_STATE", "TEMPORAL_ORDER", "DUPLICATE_EVENT", "TRACK_OVERFLOW"
    ] = Field(default="TEMPORAL_ORDER")
    message: str = Field(..., description="冲突描述")
    event_id: Optional[str] = Field(default=None, description="涉事事件ID（主）")
    related_event_id: Optional[str] = Field(default=None, description="涉事事件ID（关联）")
    character: Optional[str] = Field(default=None, description="涉事人物名")


class TimelineConflictReport(BaseModel):
    """时间线冲突检测报告。"""

    timeline_id: str
    conflict_count: int = 0
    conflicts: list[TimelineConflict] = Field(default_factory=list)
    checked_event_count: int = 0
    generated_at: str = Field(default_factory=_now_iso)
