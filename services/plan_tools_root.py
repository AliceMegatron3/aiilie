"""受控执行 — 默认 tool handler 总接线（V0.4 收口：全部白名单工具一次注入）。

把报告「四、第一批工具」的 11 个白名单工具的默认 handler 合并为一张注册表：
- 知识域：`library.search_cards` / `knowledge.consistency_check` / `library.quantize_start`
- 反思域：`reflection.view_candidates` / `reflection.view_active` / `reflection.start`
- 项目域：`project.query`
- 文档域：`document.read` / `document.draft_create`
- 审核域：`author_review.submit`
- 时间线域：`timeline.event_create`
- 任务域：`task.quantize_progress`

`PlanExecutor.execute_plan(handlers=build_default_handlers())` 即可在一个已批准计划内
调用全部受控工具。未接线的依赖（project_manager / task_manager / draft_store /
author_gate）对应工具返回结构化 DISABLED 或按 handler 语义 fail-closed。
"""
from __future__ import annotations

from typing import Any

from services.author_review_gate import make_author_review_submit_handler
from services.document_draft import DraftStore, make_document_draft_handler, make_document_read_handler
from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_tools import make_default_knowledge_handlers
from services.project_tools import make_project_query_handler
from services.reflector_tools import make_default_reflector_handlers
from services.skill_bank import SkillBank
from services.task_tools import make_quantize_progress_handler
from services.timeline_tools import make_timeline_event_create_handler


def build_default_handlers(
    store: KnowledgeClaimStore | None = None,
    bank: SkillBank | None = None,
    project_manager: Any | None = None,
    task_manager: Any | None = None,
    draft_store: DraftStore | None = None,
    author_gate: Any | None = None,
    actor: str = "author",
) -> dict[str, Any]:
    """合并全部白名单工具默认 handler（键为白名单工具 id）。"""
    handlers: dict[str, Any] = {}
    handlers.update(make_default_knowledge_handlers(store))
    handlers.update(make_default_reflector_handlers(bank))
    handlers["project.query"] = make_project_query_handler(store, draft_store, bank)
    handlers["document.read"] = make_document_read_handler(draft_store)
    handlers["document.draft_create"] = make_document_draft_handler(draft_store)
    handlers["author_review.submit"] = make_author_review_submit_handler(author_gate, actor)
    handlers["timeline.event_create"] = make_timeline_event_create_handler(project_manager)
    handlers["task.quantize_progress"] = make_quantize_progress_handler(task_manager)
    return handlers


__all__ = ["build_default_handlers"]
