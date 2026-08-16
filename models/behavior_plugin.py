"""
models/behavior_plugin.py — 行为插件数据契约(P1,专精能手施工图)
================================================================
行为插件 = 技能卡的可执行化:触发条件 + 打磨模板 + 确定性验收,
以"编辑通行"形态挂在 supervisor 串行精修链上运行。

设计要点(讨论定稿):
- 触发六维(章型/预算偏差/模式实例/情绪状态/弧线阶段/在场组合),
  未填即通配——P5/P6/P7 的维度自契约建立之初即预留;
- 状态机 candidate→gray→active→retired,来源四标记;
  浅知识(quantified)永不直接 active(三道门:作者审/灰度/验收);
- 验收为确定性检查(篇幅带/非空/非同文/无JSON包裹),
  不过验收自动回退通行前文本——保底不伤稿;
- 灰度采样确定性:hash(plugin_id, task_id) 决定命中,可复现。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PluginStatus(str, Enum):
    CANDIDATE = "CANDIDATE"  # 候选(浅知识默认态,不参与运行)
    GRAY = "GRAY"            # 灰度(按比例采样生效)
    ACTIVE = "ACTIVE"        # 转正(全量生效)
    RETIRED = "RETIRED"      # 退役


class PluginSource(str, Enum):
    CRAFTED = "CRAFTED"            # 手工精写(质量锚点)
    QUANTIFIED = "QUANTIFIED"      # 书库量化浅知识(只能进candidate)
    WEB_VERIFIED = "WEB_VERIFIED"  # 网络补全验证(P3预留)
    LEARNED = "LEARNED"            # 使用证据学习(P2闭环产物)


class PluginTrigger(BaseModel):
    """触发六维:未填即通配;多值命中任一即算命中。"""
    chapter_types: list[str] | None = None      # 章型(如 高潮章/过渡章)
    max_budget_deviation: int | None = None     # 预算偏差达到阈值触发
    pattern_instances: list[str] | None = None  # 模式实例(如 宴会/登场)
    emotion_keywords: list[str] | None = None   # 情绪状态(P5)
    arc_stages: list[str] | None = None         # 弧线阶段(P6)
    onstage_tags: list[str] | None = None       # 在场组合标签(P7,如 rival_pair)

    def matches(self, ctx: "TriggerContext") -> bool:
        if self.chapter_types and ctx.chapter_type not in self.chapter_types:
            return False
        if (self.max_budget_deviation is not None
                and abs(ctx.budget_deviation or 0) < self.max_budget_deviation):
            return False
        if self.pattern_instances and ctx.pattern_instance not in self.pattern_instances:
            return False
        if (self.emotion_keywords
                and not any(k in (ctx.emotion_state or "") for k in self.emotion_keywords)):
            return False
        if self.arc_stages and ctx.arc_stage not in self.arc_stages:
            return False
        if (self.onstage_tags
                and not any(t in (ctx.onstage_tags or []) for t in self.onstage_tags)):
            return False
        return True


class TriggerContext(BaseModel):
    """一次生成任务的触发上下文(六维快照 + 灰度采样种子)。"""
    task_id: str = ""
    chapter_type: str | None = None
    budget_deviation: int | None = None
    pattern_instance: str | None = None
    emotion_state: str | None = None
    arc_stage: str | None = None
    onstage_tags: list[str] = Field(default_factory=list)


class PluginAcceptance(BaseModel):
    """确定性验收标准(不过即回退,保底不伤稿)。"""
    min_length_ratio: float = Field(default=0.6, description="产出/原稿篇幅下限")
    max_length_ratio: float = Field(default=2.0, description="产出/原稿篇幅上限")
    require_different: bool = True
    forbid_json_wrap: bool = True

    def check(self, before: str, after: str) -> tuple[bool, str]:
        after = (after or "").strip()
        if not after:
            return False, "empty_output"
        if self.forbid_json_wrap and after[0] in "{[":
            return False, "json_wrapped"
        if self.require_different and after == (before or "").strip():
            return False, "identical_output"
        base = max(len((before or "").strip()), 1)
        ratio = len(after) / base
        if ratio < self.min_length_ratio:
            return False, "too_short"
        if ratio > self.max_length_ratio:
            return False, "too_long"
        return True, "accepted"


class BehaviorPluginSpec(BaseModel):
    """行为插件规格五要素 + 状态机 + 来源。"""
    plugin_id: str
    kind: str = Field(default="editor", description="editor=编辑通行;预留生成/检索辅助")
    name: str
    description: str = ""
    prompt_template_id: str
    prompt_override: str | None = Field(
        default=None,
        description="直接可用的打磨prompt(含{content}占位)——量化技能候选/治理晋升件的执行载体",
    )
    trigger: PluginTrigger = Field(default_factory=PluginTrigger)
    acceptance: PluginAcceptance = Field(default_factory=PluginAcceptance)
    status: PluginStatus = PluginStatus.CANDIDATE
    source: PluginSource = PluginSource.CRAFTED
    gray_percent: int = Field(default=0, ge=0, le=100)
    version: int = 1
    run_order: int = Field(default=100, description="链上执行顺序,小者先")
    created_at: str = Field(default_factory=_now)

    def gray_hit(self, task_id: str) -> bool:
        """确定性灰度采样:同(插件,任务)结果可复现。"""
        if self.status != PluginStatus.GRAY or self.gray_percent <= 0:
            return False
        seed = f"{self.plugin_id}:{task_id or 'anon'}".encode("utf-8")
        score = int(hashlib.sha1(seed).hexdigest()[:8], 16) % 100
        return score < self.gray_percent


class BehaviorPluginRunRecord(BaseModel):
    """一次打磨通行的落账(P2统计的原料)。"""
    plugin_id: str
    task_id: str = ""
    triggered: bool = False
    accepted: bool = False
    reason: str = ""
    duration_ms: int = 0
    input_chars: int = 0
    output_chars: int = 0
    ts: str = Field(default_factory=_now)
