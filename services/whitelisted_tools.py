"""受控执行智能体 — 白名单应用工具注册表（V0.4 受控执行）。

方向报告「四、执行智能体的正确起点」：第一批只开放应用内部白名单工具
（查询项目/读文档/查书卡/发起量化/查进度/启动反思/看候选/提交作者审核/
创建文档草稿/创建时间轴事件/一致性检查）。每个工具带权限等级声明。

角色：记录哪些工具允许被 Agent Plan 调用，以及调用所需的最低权限；
执行门据此 fail-closed——未注册或权限不足的工具一律拒绝，绝不回退。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable


class PermissionLevel(IntEnum):
    NONE = 0
    READ = 1
    WRITE = 2
    GOVERN = 3


@dataclass(frozen=True)
class ToolSpec:
    id: str
    description: str
    required_permission: PermissionLevel = PermissionLevel.READ
    read_only: bool = True
    scopes: tuple[str, ...] = ()   # 允许的项目作用域；空=不限


class ToolNotAllowedError(RuntimeError):
    pass


class WhitelistedToolRegistry:
    """受控工具注册表：注册 + 权限校验 + 批量校验。"""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.id or spec.id in self._specs:
            raise ValueError(f"工具标识非法或重复: {spec.id!r}")
        self._specs[spec.id] = spec

    def has(self, tool_id: str) -> bool:
        return tool_id in self._specs

    def spec(self, tool_id: str) -> ToolSpec | None:
        return self._specs.get(tool_id)

    def allowed_ids(self) -> list[str]:
        return sorted(self._specs)

    def allows(self, tool_id: str, permission: PermissionLevel) -> bool:
        """工具是否已注册且调用方权限满足其要求。"""
        spec = self._specs.get(tool_id)
        if spec is None:
            return False
        return int(permission) >= int(spec.required_permission)

    def validate(self, tool_ids: Iterable[str], permission: PermissionLevel) -> list[str]:
        """返回被拒绝的工具 id（未注册或权限不足）。空列表=全部放行。"""
        rejected: list[str] = []
        for tool_id in tool_ids:
            if not self.allows(tool_id, permission):
                rejected.append(tool_id)
        return rejected


def build_report_first_batch() -> WhitelistedToolRegistry:
    """按方向报告「四、第一批工具」构造内置白名单注册表。"""
    reg = WhitelistedToolRegistry()
    R, W, G = PermissionLevel.READ, PermissionLevel.WRITE, PermissionLevel.GOVERN
    specs = [
        ToolSpec("project.query", "查询项目", R),
        ToolSpec("document.read", "读取指定文档", R),
        ToolSpec("library.search_cards", "查询书籍和卡片", R),
        ToolSpec("library.quantize_start", "发起量化任务", W),
        ToolSpec("task.quantize_progress", "查询量化进度", R),
        ToolSpec("reflection.start", "启动反思任务", W),
        ToolSpec("reflection.view_candidates", "查看候选技能", R),
        ToolSpec("author_review.submit", "提交作者审核", W),
        ToolSpec("document.draft_create", "创建文档草稿", W),
        ToolSpec("timeline.event_create", "创建时间轴事件", W),
        ToolSpec("knowledge.consistency_check", "运行知识一致性检查", R),
    ]
    for s in specs:
        reg.register(s)
    return reg


_default_registry: WhitelistedToolRegistry | None = None


def get_report_registry() -> WhitelistedToolRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = build_report_first_batch()
    return _default_registry


def assert_tools_allowed(tools: Iterable[str], permission: PermissionLevel) -> None:
    """校验并通过；任一工具被拒绝即抛 ToolNotAllowedError（fail-closed）。"""
    rejected = get_report_registry().validate(tools, permission)
    if rejected:
        raise ToolNotAllowedError(
            f"计划引用了未注册或权限不足的工具: {sorted(rejected)}"
        )


__all__ = [
    "PermissionLevel",
    "ToolNotAllowedError",
    "ToolSpec",
    "WhitelistedToolRegistry",
    "assert_tools_allowed",
    "build_report_first_batch",
    "get_report_registry",
]