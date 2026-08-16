"""
models/ensemble.py — 群像层数据契约(P7)
==========================================
"每个角色都有自己的日子":生活轨道(滚动社会逻辑链快照)、
关系账本(基线/事件挂账/按深浅回摆三态力学)、派系(事件作用域
临时容器)、声纹(角色说话方式)。

关系力学(讨论定稿):基线=情感账户余额(-100敌..+100挚);
大事件期间因身份与需求强制对位产生挂账增量;事件结束挂账清算,
关系按余额深浅回摆——深交的敌对完还是朋友,浅交的同盟完就是路人。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pair_key(a: str, b: str) -> str:
    """无序对键:同两人关系唯一。"""
    x, y = sorted([a, b])
    return f"{x}|{y}"


class LifeTrack(BaseModel):
    """角色生活轨道:社会逻辑链的滚动快照(章末/事件后更新)。"""
    character_id: str
    project_id: str
    name: str = ""
    aliases: list[str] = Field(
        default_factory=list,
        description="别名/字/尊称(如 玄德/孟德),用于在场识别匹配",
    )
    position: str = Field(default="", description="当前所处位置/阵营/处境")
    livelihood: str = Field(default="", description="营生(靠什么过活)")
    attachments: list[str] = Field(default_factory=list, description="牵挂(人/物/念)")
    resources: list[str] = Field(default_factory=list, description="掌握的资源")
    debts: list[str] = Field(default_factory=list, description="欠情/欠债/受制")
    needs: list[str] = Field(default_factory=list, description="当前最迫切的需求")
    updated_chapter: int = Field(default=1, ge=1)


class EventDelta(BaseModel):
    """事件挂账:大事件期间因身份与需求强制结成的对位增量。"""
    event_id: str
    delta: int = Field(description="临时增量,正=拉近,负=疏远/敌对")
    reason: str = ""
    active: bool = True


class RelationshipLedgerEntry(BaseModel):
    """关系账本条目:基线余额 + 事件挂账 + 回摆力学。"""
    entry_id: str = Field(default_factory=lambda: _gen_id("rel"))
    project_id: str
    character_a: str
    character_b: str
    baseline: int = Field(default=0, ge=-100, le=100, description="情感账户余额")
    events: list[EventDelta] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)

    @property
    def pair(self) -> str:
        return pair_key(self.character_a, self.character_b)

    def current(self) -> int:
        """当前关系 = 基线 + 活跃挂账。"""
        value = self.baseline + sum(e.delta for e in self.events if e.active)
        return max(-100, min(100, value))

    def depth_factor(self) -> float:
        """余额深浅→结晶比例:深交(敌或友)保留更多事件改变。"""
        return min(0.5, abs(self.baseline) / 200.0)

    def close_event(self, event_id: str) -> str:
        """事件清算:挂账撤除,按深浅把部分事件改变结晶进基线;返回回摆提醒。"""
        closed = [e for e in self.events if e.event_id == event_id and e.active]
        if not closed:
            return ""
        before = self.current()
        crystallized = 0
        for e in closed:
            keep = round(e.delta * self.depth_factor())
            crystallized += keep
            e.active = False
        self.baseline = max(-100, min(100, self.baseline + crystallized))
        after = self.current()
        arrow = "回摆" if abs(after) < abs(before) else "加深"
        return (
            f"{self.character_a}×{self.character_b}:事件前{before}→清算后{after}"
            f"({arrow};余额深浅结晶{crystallized:+d})"
        )


class FactionCard(BaseModel):
    """派系:事件作用域的临时容器,事件终了解散归档。"""
    faction_id: str = Field(default_factory=lambda: _gen_id("fct"))
    project_id: str
    event_id: str
    name: str
    members: list[str] = Field(default_factory=list)
    stance: dict[str, str] = Field(default_factory=dict, description="成员→立场简述")
    status: str = Field(default="active", description="active/dissolved")
    created_at: str = Field(default_factory=_now)


class VoiceCard(BaseModel):
    """角色声纹:说话习惯/决策风格/视角质地(+对白标本引用)。"""
    character_id: str
    project_id: str
    name: str = ""
    speech_habits: str = Field(default="", description="句长/语气词/称呼习惯/回避词")
    decision_style: str = Field(default="", description="果决/谋定/犹疑/随势")
    pov_texture: str = Field(default="", description="视角质地(重环境/重人情/重利害)")
    specimen_refs: list[str] = Field(default_factory=list, description="对白标本卡引用(P5)")
    updated_at: str = Field(default_factory=_now)
