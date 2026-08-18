"""core/contract.py — 统一核心领域契约（Batch 0：基线与边界）

目标：把「对外 API 包络」与「后台任务生命周期契约」收敛为唯一、可测试的类型定义，
消除各处手写 dict / 字段命名漂移。所有系统组件（api / services / worker）应基于本模块
类型进行输入输出校验，而不是依赖各模块自行拼 dict。

约定：
- 响应包络唯一形状：`{success, data, message, error_code}`（与 core/response.ok/fail 一致）。
- 任务状态枚举复用 `models.task.TaskStatus`（单一事实源，禁止重复定义第二套枚举）。
- 本模块为**契约层**，只声明形状与基本校验，不承载业务逻辑。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from models.task import TaskStatus  # 单一事实源：任务生命周期枚举

T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 统一 API 响应包络 ──────────────────────────────────────────────
class ApiEnvelope(BaseModel, Generic[T]):
    """所有 /api/v1 接口的统一返回包络（与 core.response.ok/fail 同构）。

    调用方可直接用 `ApiEnvelope[MyModel](...)` 标注 data 的具体类型，
    也可用 `ApiEnvelope.model_validate(ok(...))` 复用 core.response 构造器。
    """

    success: bool = True
    data: T | None = None
    message: str = "success"
    error_code: str | None = None

    @classmethod
    def ok(cls, data: Any = None, message: str = "success") -> "ApiEnvelope[T]":
        return cls(success=True, data=data, message=message, error_code=None)

    @classmethod
    def fail(
        cls,
        message: str,
        *,
        error_code: str = "BUSINESS_ERROR",
        data: Any = None,
    ) -> "ApiEnvelope[T]":
        return cls(success=False, data=data, message=message, error_code=error_code)


# ── 后台任务生命周期契约 ───────────────────────────────────────────
class TaskSubmission(BaseModel):
    """提交任务的统一契约（与 BasePipelineTask 的提交侧字段对齐）。

    payload 为任意业务字典（如 quantize 的 book_id、code_execution 的
    request_payload 等）。idempotency_key 非空时，相同键不允许产生重复副作用。
    """

    task_id: str = Field(..., min_length=1)
    task_type: str = Field(..., min_length=1)
    priority: int = Field(default=4, ge=1, le=7)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class TaskCheckpoint(BaseModel):
    """任务执行的检查点/进度契约（worker 幂等重放的关键）。

    一个任务可能有多个 checkpoint（按 segment / step 定位）；同一 task_id +
    segment_id 的 checkpoint 重放必须幂等。output_path 指向产物所在磁盘位置。
    """

    task_id: str = Field(..., min_length=1)
    segment_id: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    progress: dict[str, Any] = Field(default_factory=dict)
    output_path: str | None = None
    checkpoint_at: datetime = Field(default_factory=_now)


class TaskHandler(BaseModel):
    """任务 handler 的能力契约：声明「哪种 task_type 由哪个 handler 处理」。

    required=True 表示该 task_type 必须有可用 handler 才能被评为 COMPLETED；
    available 为运行时实际可用性（如外部 provider 未配置时应为 False，任务须 FAILED
    而非静默跳过）。
    """

    task_type: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    required: bool = True
    available: bool = False


class TaskAuditEvent(BaseModel):
    """任务审计事件契约：记录驱动 COMPLETED 所需的审计落盘条目。"""

    event_id: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    actor: str = Field(..., min_length=1)  # 如 "user:xxx" / "system"
    action: str = Field(..., min_length=1)  # 如 "task.submit" / "segment.completed"
    outcome: str = Field(..., pattern="^(success|failure|disabled|skipped)$")
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_now)