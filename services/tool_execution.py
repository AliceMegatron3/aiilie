"""受控执行 — 白名单工具执行门（V0.4：幂等 + 超时 + 审计）。

方向报告「四、每个工具必须经过」在 whitelist/audit 之外还要求：
    参数校验 / 资源配额(超时) / 幂等检查 / 可取消。

本模块把 whitelist(权限) + 超时(资源) + 幂等 + 审计(终态 OK/ERROR) 组合为
`IdempotentToolRunner.run()` 的执行门：
- 工具必须已注册于白名单且调用方权限足够，否则 fail-closed 拒绝；
- args 必须为非空 dict；
- 以 (tool, args_digest[, run_key]) 做幂等键：同一键在跑时返回 RUNNING，避免重复副作用；
- 每次调用包 `asyncio.wait_for` 超时（默认 30s），超时记 ERROR 审计；
- 从"开始/成功/失败"写入注入的审计轨（终态 OK/ERROR 回流）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from services.plan_audit import digest_args
from services.whitelisted_tools import PermissionLevel, WhitelistedToolRegistry, get_report_registry

logger = logging.getLogger(__name__)


class ToolExecutionDeniedError(RuntimeError):
    pass


class ToolExecutionTimeoutError(RuntimeError):
    pass


Handler = Callable[[dict[str, Any]], Awaitable[Any]]
AuditFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class RunResult:
    tool: str
    run_id: str
    status: str            # RUNNING / OK / ERROR / TIMEOUT / DENIED / INVALID
    idempotent: bool = False
    result: Any | None = None
    message: str = ""


class IdempotentToolRunner:
    """白名单工具执行门：权限 + 参数 + 幂等 + 超时 + 审计。"""

    def __init__(
        self,
        *,
        timeout_seconds: int = 30,
        whitelist: WhitelistedToolRegistry | None = None,
        audit: AuditFn | None = None,
    ) -> None:
        self.timeout_seconds = max(1, timeout_seconds)
        self.whitelist = whitelist or get_report_registry()
        self._audit = audit
        self._runs: dict[tuple[str, str], asyncio.Task] = {}
        self._by_run_id: dict[str, asyncio.Task] = {}

    async def run(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        actor: str,
        permission: PermissionLevel,
        handler: Handler,
        plan_key: str = "",
    ) -> RunResult:
        """执行白名单工具（带门禁）。handler 为注入的真实能力调用。"""
        # 1) 白名单 + 权限（fail-closed）
        if not self.whitelist.allows(tool, permission):
            return await self._finish(tool, actor, RunResult(tool, "", "DENIED", message="未注册或权限不足"))
        # 2) 参数校验：必须为 dict（允许空参工具；具体必填字段由 handler 校验）
        if not isinstance(args, dict):
            return await self._finish(tool, actor, RunResult(tool, "", "INVALID", message="参数必须为 dict"))
        # 3) 幂等：同 (tool, digest) 在跑 → RUNNING；run_id 由键派生，重复调用共享以便取消
        digest = digest_args(args) + (f"|{plan_key}" if plan_key else "")
        key = (tool, digest)
        run_id = f"run_{tool}_{digest[:16]}"
        existing = self._runs.get(key)
        if existing is not None and not existing.done():
            return RunResult(tool, run_id, "RUNNING", idempotent=True, message="同参任务已在执行")
        # 4) 发起执行（带超时）
        await self._emit(tool, actor, "start", run_id, status="RUNNING", digest=digest)
        coro = self._run_with_timeout(tool, actor, run_id, digest, handler, args)
        task = asyncio.create_task(coro)
        self._runs[key] = task
        self._by_run_id[run_id] = task
        try:
            try:
                result = await task
            except asyncio.CancelledError:
                result = RunResult(tool, run_id, "CANCELLED", message="执行已被取消")
                await self._emit(tool, actor, "cancelled", run_id, "CANCELLED", digest, message="执行已被取消")
        finally:
            self._runs.pop(key, None)
            self._by_run_id.pop(run_id, None)
        return result

    def cancel(self, run_id: str) -> bool:
        """取消指定 run_id 的执行（若仍在运行）。可取消处理（方向报告四）。"""
        task = self._by_run_id.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _run_with_timeout(self, tool, actor, run_id, digest, handler, args) -> RunResult:
        try:
            value = await asyncio.wait_for(handler(args), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            logger.warning("工具 %s 执行超时(>%ss)", tool, self.timeout_seconds)
            return await self._finish(
                tool, actor,
                RunResult(tool, run_id, "TIMEOUT", result=None,
                          message=f"执行超时（>{self.timeout_seconds}s）"),
                digest=digest,
            )
        except Exception as exc:  # noqa: BLE001
            return await self._finish(
                tool, actor,
                RunResult(tool, run_id, "ERROR", result=None, message=str(exc)),
                digest=digest,
            )
        return await self._finish(
            tool, actor,
            RunResult(tool, run_id, "OK", result=value, message=""),
            digest=digest,
        )

    async def _finish(
        self, tool: str, actor: str, res: RunResult, digest: str = ""
    ) -> RunResult:
        action = {"OK": "ok", "ERROR": "error", "TIMEOUT": "error"}.get(res.status, "denied")
        await self._emit(tool, actor, action, res.run_id, status=res.status, digest=digest, message=res.message)
        return res

    async def _emit(self, tool, actor, action, run_id, status, digest, message=""):
        if self._audit is None:
            return
        await self._audit(
            {
                "tool": tool,
                "actor": actor,
                "action": action,
                "run_id": run_id,
                "status": status,
                "digest": digest,
                "message": message,
            }
        )


__all__ = ["IdempotentToolRunner", "RunResult", "ToolExecutionDeniedError", "ToolExecutionTimeoutError"]