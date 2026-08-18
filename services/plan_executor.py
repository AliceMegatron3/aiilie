"""受控执行 — 计划执行器（V0.4：核心调度器执行 + 每步审计/幂等/超时）。

方向报告「四、执行链」：用户目标 → Agent 生成计划 → 作者确认 → 核心调度器执行
→ 每一步写入审计事件 → 返回结果和证据。

本模块把已批准计划的**冻结步骤**逐个送入白名单工具执行门
（`IdempotentToolRunner`：权限+参数+幂等+超时+审计终态），并汇总每步结果作为
“证据”返回。安全不变量：
- 只在 `plan.execute(version, actor)` 通过「批准门 + 主体一致」后才执行；
- 未接线真实 handler 的工具 → DENIED（fail-closed，不静默跳过）；
- 未注册/权限不足工具由 runner 白名单门直接拒绝。
"""
from __future__ import annotations

from typing import Any

from services.agent_plan import AgentPlan
from services.plan_audit import AuditEvent, PlanAuditStore
from services.tool_execution import IdempotentToolRunner, RunResult
from services.whitelisted_tools import PermissionLevel, WhitelistedToolRegistry, get_report_registry

# 处理签名：dict[str, Any] -> Awaitable[Any]
Handler = Any


class PlanExecutor:
    """把已批准计划的冻结步骤编排到白名单工具执行门。"""

    def __init__(
        self,
        *,
        runner: IdempotentToolRunner | None = None,
        whitelist: WhitelistedToolRegistry | None = None,
        audit_store: PlanAuditStore | None = None,
    ) -> None:
        self.runner = runner or IdempotentToolRunner()
        self.whitelist = whitelist or get_report_registry()
        self.audit_store = audit_store  # 注入则把每步终态审计回流（可回放）

    async def execute_plan(
        self,
        plan: AgentPlan,
        *,
        version: int,
        actor: str,
        handlers: dict[str, Handler],
        permission: PermissionLevel = PermissionLevel.GOVERN,
    ) -> list[RunResult]:
        """执行已批准版本的步骤，返回每步结果（证据）。未批准/主体不符抛 PlanGateError。"""
        steps = plan.execute(version, actor)  # 批准门 + 主体一致门
        results: list[RunResult] = []
        for step in steps:
            if not self.whitelist.allows(step.tool, permission):
                result = RunResult(step.tool, "", "DENIED", message="未注册或权限不足")
            else:
                handler = handlers.get(step.tool)
                if handler is None:
                    result = RunResult(step.tool, "", "DENIED", message="该工具未在本宿主接线")
                else:
                    result = await self.runner.run(
                        step.tool,
                        step.args,
                        actor=actor,
                        permission=permission,
                        handler=handler,
                        plan_key=plan.plan_id,
                    )
            results.append(result)
            # 审计终态回流：dispatched/PENDING(API) 之后记 completed/终态，构成完整可回放轨
            if self.audit_store is not None:
                await self.audit_store.append(
                    AuditEvent(
                        plan_id=plan.plan_id,
                        version=version,
                        actor=actor,
                        tool=step.tool,
                        step_id=step.step_id,
                        action="completed",
                        status=result.status,
                        note=result.message or "执行终态已回流",
                    )
                )
        return results


__all__ = ["PlanExecutor"]