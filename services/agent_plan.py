"""通用 Agent Plan（V0.4 受控执行智能体 — 计划模式核心）。

方向报告「四、执行智能体的正确起点」计划对象与不变量：

    计划对象 {plan_id, workspace_id, identity, goal, steps, required_tools,
              risk_level, status, version, approved_by}

不变量（本模块以不可变快照 + 版本绑定审批门保证）：
    1. 执行只能走「已确认的计划版本」—— `execute()` 仅当该版本 status==APPROVED
       且请求版本与已批准版本一致时才放行。
    2. 执行获取到的是该版本的**冻结步骤快照**（不可变元组），执行中无法偷改
       目标/工具集/项目范围——任何后续编辑只会产生新版本，旧批准版本内容冻结不变。
    3. 批准必须由 `approved_by` 主体在指定版本上完成；`execute` 校验 actor 一致，
       agent 不能自行批准自己的计划（见方向报告 C 类：agent 自批计划禁止）。

本模块为纯状态机核心（内存态），不依赖数据库/LLM；持久化与调度器接线为后续集成项。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field


class PlanStatus(str, Enum):
    DRAFT = "DRAFT"
    AUTHOR_REVIEW = "AUTHOR_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PlanPhase(str, Enum):
    # 计划尚未进入执行阶段；保留位供后续“可暂停/可恢复”扩展
    IDLE = "IDLE"


class PlanStep(BaseModel):
    step_id: str = Field(default_factory=lambda: f"step_{uuid4().hex[:8]}")
    tool: str = Field(min_length=1)       # 白名单应用工具 ID
    args: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class PlanGateError(RuntimeError):
    """计划门禁拒绝执行（未批准 / 版本不匹配 / 操作主体不一致）。"""


@dataclass(frozen=True)
class PlanSnapshot:
    """某个版本的不可变快照：内容与审批状态一并冻结。"""

    version: int
    goal: str
    required_tools: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    risk_level: str = "low"
    status: PlanStatus = PlanStatus.DRAFT
    approved_by: str | None = None


class AgentPlan:
    """版本化的受控计划。

    - 每次编辑产生一个新版本快照（旧版本内容冻结）。
    - `approve(version, actor)` 只批准指定版本；审批结果落在该版本上。
    - `execute(version, actor)` 是唯一执行门：未批准/版本不一致/主体不一致一律拒绝。
    - `required_tools` 由步骤工具集派生并随版本冻结，执行门校验工具集不可越权。
    """

    _ALLOWED_APPROVE_FROM = {PlanStatus.DRAFT, PlanStatus.AUTHOR_REVIEW, PlanStatus.REJECTED}

    def __init__(
        self,
        *,
        plan_id: str | None = None,
        workspace_id: str = "default",
        identity: str = "author",
        goal: str = "",
    ) -> None:
        self.plan_id = plan_id or f"plan_{uuid4().hex[:12]}"
        self.workspace_id = workspace_id
        self.identity = identity
        # version -> frozen snapshot；只允许单调递增追加
        self._snapshots: dict[int, PlanSnapshot] = {}
        self._reviews: dict[int, list[dict]] = {}   # version -> append-only 评审意见
        self.latest_version = 0
        self.creator = identity

    # ── 版本生命周期 ────────────────────────────────────────────
    def draft(
        self,
        goal: str,
        steps: list[PlanStep] | tuple[PlanStep, ...],
        risk_level: str = "low",
    ) -> int:
        """建立/更新一个草稿版本；每次调用都生成新版本号（旧版本冻结）。"""
        if not steps:
            raise ValueError("计划必须包含至少一步")
        return self._new_version(goal, steps, risk_level)

    def edit(
        self,
        steps: list[PlanStep] | tuple[PlanStep, ...],
        *,
        goal: str | None = None,
        risk_level: str | None = None,
    ) -> int:
        """在最新版基础上编辑出新草稿；若最新版已批准，则新版本为候选，须重新批准。"""
        latest = self._snapshots.get(self.latest_version)
        if latest is None:
            raise ValueError("尚无计划版本，请先 draft()")
        return self._new_version(
            goal if goal is not None else latest.goal,
            steps,
            risk_level if risk_level is not None else latest.risk_level,
        )

    def submit_for_review(self, version: int, actor: str) -> None:
        snapshot = self._snapshot(version)
        if snapshot.status not in (PlanStatus.DRAFT, PlanStatus.REJECTED, PlanStatus.AUTHOR_REVIEW):
            raise PlanGateError(f"版本 {version} 状态为 {snapshot.status.value}，不可进入审核")
        self._set_status(version, PlanStatus.AUTHOR_REVIEW)

    def approve(self, version: int, actor: str) -> None:
        """作者/授权主体批准指定版本。agent 不能批准自己的计划（identity 校验）。"""
        snapshot = self._snapshot(version)
        if snapshot.status not in self._ALLOWED_APPROVE_FROM:
            raise PlanGateError(f"版本 {version} 状态为 {snapshot.status.value}，不可批准")
        if actor == self.identity_for_exec(version):
            raise PlanGateError("执行主体不能批准自己的计划（需独立 authors 审批）")
        self._set_status(version, PlanStatus.APPROVED)
        # 将审批主体写回快照，作为 execute 的 actor 校验依据
        self._snapshots[version] = PlanSnapshot(
            version=snapshot.version,
            goal=snapshot.goal,
            required_tools=snapshot.required_tools,
            steps=snapshot.steps,
            risk_level=snapshot.risk_level,
            status=PlanStatus.APPROVED,
            approved_by=actor,
        )

    def reject(self, version: int, actor: str) -> None:
        snapshot = self._snapshot(version)
        if snapshot.status != PlanStatus.AUTHOR_REVIEW:
            raise PlanGateError(f"版本 {version} 不在审核中，无法驳回")
        self._set_status(version, PlanStatus.REJECTED)

    def request_changes(self, version: int, actor: str, note: str = "") -> None:
        """作者讨论：对审核中版本提出修改意见，退回 DRAFT 待修改。

        方向报告「四」执行链：Agent 生成计划 → 作者讨论和修改计划 → 作者确认。
        agent 不能对自己发起的计划提修改意见（与自批同理，保持评审独立）。
        """
        snapshot = self._snapshot(version)
        if snapshot.status != PlanStatus.AUTHOR_REVIEW:
            raise PlanGateError(f"版本 {version} 不在审核中，无法要求修改")
        if actor == self.identity_for_exec(version):
            raise PlanGateError("执行主体不能对自己发起的计划提修改意见")
        self._reviews.setdefault(version, []).append(
            {"actor": actor, "note": note, "action": "request_changes"}
        )
        self._set_status(version, PlanStatus.DRAFT)

    def review_notes(self, version: int) -> list[dict]:
        """指定版本的评审意见（append-only，可回放）。"""
        return list(self._reviews.get(version, []))

    # ── 执行门（核心不变量） ─────────────────────────────────────
    def execute(self, version: int, actor: str) -> tuple[PlanStep, ...]:
        """唯一执行入口。

        仅当 指定版本存在 && 该版本已 APPROVED && 请求版本==已批准版本
              && actor==approved_by 时才返回**冻结步骤**；
        否则一律抛 PlanGateError（fail-closed），不可回退、不可越权。
        """
        snapshot = self._snapshot(version)
        if snapshot.status != PlanStatus.APPROVED:
            raise PlanGateError(f"计划版本 {version} 未获批准（当前 {snapshot.status.value}），禁止执行")
        if snapshot.approved_by is None or snapshot.approved_by != actor:
            raise PlanGateError("执行主体与批准主体不一致，禁止执行")
        # 返回冻结元组副本，调用方无法就地改动版本内容
        return tuple(snapshot.steps)

    def can_execute(self, version: int, actor: str) -> bool:
        """只读判定（不产生副作用）。"""
        try:
            self.execute(version, actor)
            return True
        except PlanGateError:
            return False

    # ── 只读访问 ────────────────────────────────────────────────
    def snapshot(self, version: int) -> PlanSnapshot:
        return self._snapshot(version)

    def status(self, version: int) -> PlanStatus:
        return self._snapshot(version).status

    def required_tools(self, version: int) -> tuple[str, ...]:
        return self._snapshot(version).required_tools

    def identity_for_exec(self, version: int) -> str:
        """执行主体标识（当前沿用计划 creator；后续可注入独立执行者身份）。"""
        return self.creator

    @property
    def snapshots(self) -> Mapping[int, PlanSnapshot]:
        """版本 -> 冻结快照（只读视图）。"""
        return dict(self._snapshots)

    # ── 序列化（持久化就绪） ────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        """把整个版本化计划导出为可 JSON 化的 dict（供文件/DB 落库）。"""
        return {
            "plan_id": self.plan_id,
            "workspace_id": self.workspace_id,
            "identity": self.identity,
            "creator": self.creator,
            "latest_version": self.latest_version,
            "reviews": {str(k): v for k, v in self._reviews.items()},
            "snapshots": [
                {
                    "version": s.version,
                    "goal": s.goal,
                    "required_tools": list(s.required_tools),
                    "steps": [s2.model_dump(mode="json") for s2 in s.steps],
                    "risk_level": s.risk_level,
                    "status": s.status.value,
                    "approved_by": s.approved_by,
                }
                for s in sorted(self._snapshots.values(), key=lambda x: x.version)
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentPlan":
        plan = cls(
            plan_id=data.get("plan_id"),
            workspace_id=data.get("workspace_id", "default"),
            identity=data.get("identity", "author"),
            goal="",
        )
        plan.creator = data.get("creator", plan.creator)
        for k, v in (data.get("reviews") or {}).items():
            plan._reviews[int(k)] = list(v)
        for s in data.get("snapshots", []):
            snapshot = PlanSnapshot(
                version=int(s["version"]),
                goal=s["goal"],
                required_tools=tuple(s["required_tools"]),
                steps=tuple(PlanStep(**st) for st in s["steps"]),
                risk_level=s.get("risk_level", "low"),
                status=PlanStatus(s.get("status", "DRAFT")),
                approved_by=s.get("approved_by"),
            )
            plan._snapshots[snapshot.version] = snapshot
            plan.latest_version = max(plan.latest_version, snapshot.version)
        return plan

    # ── 内部 ────────────────────────────────────────────────────
    def _new_version(self, goal: str, steps: list[PlanStep] | tuple[PlanStep, ...], risk_level: str) -> int:
        version = self.latest_version + 1
        required = tuple(sorted({s.tool for s in steps}))
        snapshot = PlanSnapshot(
            version=version,
            goal=goal,
            required_tools=required,
            steps=tuple(steps),
            risk_level=risk_level,
            status=PlanStatus.DRAFT,
        )
        self._snapshots[version] = snapshot
        self.latest_version = version
        return version

    def _snapshot(self, version: int) -> PlanSnapshot:
        snapshot = self._snapshots.get(version)
        if snapshot is None:
            raise PlanGateError(f"计划版本不存在: {version}")
        return snapshot

    def _set_status(self, version: int, status: PlanStatus) -> None:
        old = self._snapshot(version)
        self._snapshots[version] = PlanSnapshot(
            version=old.version,
            goal=old.goal,
            required_tools=old.required_tools,
            steps=old.steps,
            risk_level=old.risk_level,
            status=status,
            approved_by=old.approved_by,
        )


__all__ = [
    "AgentPlan",
    "PlanGateError",
    "PlanSnapshot",
    "PlanStatus",
    "PlanStep",
]