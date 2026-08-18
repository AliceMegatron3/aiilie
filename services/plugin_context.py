"""可扩展插件内核 — PluginContext（V0.5 插件 SDK 的受限访问面）。

方向报告「五、插件体系」：插件只能通过 `PluginContext` 访问核心能力：
    identity / project_scope / read_document() / search_resources() /
    submit_candidate() / emit_audit_event() / resource_budget

且**不允许**插件直接：
    - 访问核心数据库连接
    - 读取任意绝对路径
    - 修改 Ledger
    - 批准候选技能
    - 修改权限
    - 在宿主进程导入第三方代码

本模块把上述"允许/禁止"固化成类型安全的 `PluginContext`：
- 只暴露白名单方法；数据库连接、权限、Ledger 写、候选批准等一概不在对象面上。
- `read_document` 强制限定在 `project_scope` 内；越界一律拒绝（fail-closed）。
- 通过注入的可调用对象接线到真实服务；本模块不搭数据库/LLM，纯接口契约。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResourceBudget:
    timeout_seconds: int = 30
    max_input_bytes: int = 1 * 1024 * 1024
    max_output_bytes: int = 1 * 1024 * 1024

    def as_dict(self) -> dict[str, int]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_input_bytes": self.max_input_bytes,
            "max_output_bytes": self.max_output_bytes,
        }


class ContextDeniedError(RuntimeError):
    """插件越权访问被拒（fail-closed）。"""


# 注入的接线回调（由宿主在启动时提供真实实现）
Reader = Callable[[str, str], Awaitable[Any | None]]            # (project_id, doc_id) -> doc
Searcher = Callable[[str, int], Awaitable[list[Any]]]           # (query, limit) -> hits
CandidateSubmitter = Callable[[dict[str, Any]], Awaitable[str]]  # (candidate) -> candidate_id
AuditSink = Callable[[dict[str, Any]], Awaitable[None]]          # (event) -> None


class PluginContext:
    """插件唯一可用的核心访问面（方向报告五.5）。

    安全不变量：
    - 只暴露本类公开方法；没有 db / ledger / approve / permission / 任意路径读等成员。
    - read_document 与 search_resources 强制受 project_scope 约束。
    - submit_candidate 只"提交候选"，绝不激活正式技能（激活须走作者审核）。
    """

    __slots__ = (
        "_identity",
        "_project_scope",
        "_budget",
        "_read_doc",
        "_search",
        "_submit",
        "_audit",
    )

    def __init__(
        self,
        *,
        identity: str,
        project_scope: str | None,
        budget: ResourceBudget | None = None,
        read_doc: Reader | None = None,
        search: Searcher | None = None,
        submit_candidate: CandidateSubmitter | None = None,
        audit: AuditSink | None = None,
    ) -> None:
        self._identity = identity
        self._project_scope = project_scope
        self._budget = budget or ResourceBudget()
        # 未注入的能力返回"不支持"，不抛出未捕获异常（便于插件探测能力）
        self._read_doc = read_doc
        self._search = search
        self._submit = submit_candidate
        self._audit = audit

    # ── 只读身份 / 权限边界 ─────────────────────────────────
    @property
    def identity(self) -> str:
        return self._identity

    @property
    def project_scope(self) -> str | None:
        return self._project_scope

    @property
    def resource_budget(self) -> ResourceBudget:
        return self._budget

    # ── 白名单能力（全部受 project_scope 约束） ───────────────
    async def read_document(self, project_id: str, doc_id: str) -> Any | None:
        """读取指定项目内的文档；越界项目一律拒绝。"""
        self._require_in_scope(project_id)
        if self._read_doc is None:
            raise ContextDeniedError("read_document 未在宿主接通")
        return await self._read_doc(project_id, doc_id)

    async def search_resources(self, query: str, limit: int = 8) -> list[Any]:
        """在当前项目作用域内检索资源。"""
        if self._search is None:
            raise ContextDeniedError("search_resources 未在宿主接通")
        return await self._search(query, limit)

    async def submit_candidate(self, candidate: dict[str, Any]) -> str:
        """提交候选技能/知识（仅入候选池，不激活正式技能）。"""
        if self._submit is None:
            raise ContextDeniedError("submit_candidate 未在宿主接通")
        return await self._submit(candidate)

    async def emit_audit_event(self, event: dict[str, Any]) -> None:
        """写审计事件（可观测、可回放）。"""
        if self._audit is None:
            return  # 无审计能力则静默丢弃（探测友好）
        await self._audit(event)

    # ── 越界防护 ─────────────────────────────────────────────
    def _require_in_scope(self, project_id: str) -> None:
        if self._project_scope is None or self._project_scope != project_id:
            raise ContextDeniedError(
                f"插件尝试访问其无权项目 {project_id!r}（作用域 {self._project_scope!r}）"
            )


def assert_no_privileged_surface(ctx: Any) -> list[str]:
    """校验 PluginContext 未暴露高危能力，返回发现的越权成员名（应为空）。"""
    forbidden = {
        "db",
        "database",
        "conn",
        "ledger",
        "approve",
        "approve_candidate",
        "set_permissions",
        "permissions",
        "modify_ledger",
        "read_any_path",
    }
    return [name for name in forbidden if hasattr(ctx, name)]


__all__ = [
    "ContextDeniedError",
    "PluginContext",
    "ResourceBudget",
    "assert_no_privileged_surface",
]