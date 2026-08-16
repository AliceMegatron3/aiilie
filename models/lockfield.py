"""
models/lockfield.py — 世界观锁定场数据契约(阶段4,讨论稿20260816 第六章)
==========================================================================
锁定场 = 一组命名、版本化的卡片筛选规则,决定生成时"现实是什么"。

设计要点(决策台账 #18/#19/#20/#21):
- 类型规则:锁定库内 rule_strength=hard 且 status=approved 且
  evidence≥min_evidence 的卡 → must 档(环境锁定);
- 优先级序:project > 锁定库 > global(作者产出可覆盖史实,
  但须走偏离登记);
- 版本演化:每次调整生成新版本可回滚(演化有编年史);
- 偏离登记:检测建议+作者确认,PENDING 状态不生效。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

_EVIDENCE_ORDER = ["A", "B", "C", "D"]


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def evidence_at_least(level: str, floor: str) -> bool:
    """证据等级比较:A 最强;level 不弱于 floor 时为真。"""
    try:
        return _EVIDENCE_ORDER.index(level) <= _EVIDENCE_ORDER.index(floor)
    except ValueError:
        return False


class LockFieldTypeRule(BaseModel):
    """历史类规矩锁定的类型规则。"""
    min_evidence: Literal["A", "B", "C", "D"] = "B"
    require_rule_strength: str = "hard"
    require_status: str = "approved"


class LockFieldConfig(BaseModel):
    """锁定场配置(单场+版本演化,台账#21)。"""
    field_id: str = Field(default_factory=lambda: _gen_id("lf"))
    project_id: str
    name: str = Field(default="", description="如 乱世双璧·正史基底")
    locked_library_ids: list[str] = Field(default_factory=list)
    type_rules: LockFieldTypeRule = Field(default_factory=LockFieldTypeRule)
    priority_order: list[str] = Field(
        default_factory=lambda: ["project", "locked_library", "global"]
    )
    conflict_strategy: Literal["evidence_first", "source_priority", "manual"] = "evidence_first"
    version: int = 1
    updated_at: str = Field(default_factory=_now)


class LockFieldVersionEntry(BaseModel):
    """版本日志条目:演化的编年史。"""
    entry_id: str = Field(default_factory=lambda: _gen_id("lfv"))
    field_id: str
    version: int
    change_type: Literal["create", "update_locks", "update_rules", "rename"] = "update_locks"
    detail: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class MaterializedMustSet(BaseModel):
    """物化的 must 集:锁定场加载时预算,生成时不再全库过滤。"""
    field_id: str
    version: int
    card_ids: list[str] = Field(default_factory=list)
    cards: list[dict] = Field(default_factory=list)
    total: int = 0
    digest: str = Field(default="", description="card_ids 稳定哈希,校验前缀缓存稳定性")
    built_at: str = Field(default_factory=_now)


class DivergenceStatus(str, Enum):
    PENDING = "PENDING"        # 检测建议,待作者确认
    CONFIRMED = "CONFIRMED"    # 作者确认入册(历史"改变"且记得从哪改变)
    RETRACTED = "RETRACTED"    # 作者驳回


class DivergenceRecord(BaseModel):
    """偏离点登记:project 内容对锁定史实的覆盖,带溯源。"""
    divergence_id: str = Field(default_factory=lambda: _gen_id("div"))
    project_id: str
    field_id: str | None = None
    description: str
    diverged_from_card_id: str | None = Field(default=None, description="被覆盖的史实卡")
    chapter_number: int | None = Field(default=None)
    command_context: str = Field(default="", description="登记时的作者命令语境")
    status: DivergenceStatus = DivergenceStatus.PENDING
    created_at: str = Field(default_factory=_now)
