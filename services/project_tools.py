"""受控执行 — 项目域默认 tool handlers（V0.4 第一批工具：project.query）。

方向报告「四、第一批工具」含"查询项目"。本模块提供确定性（非 LLM）`project.query`：
聚合知识主张库、文档草稿库、技能版本库的运行态摘要（如主张数/草稿数/激活技能/
候选数），作为项目层面的可执行状态视图。所有 store 均文件态（safe_join 防穿越）。
"""
from __future__ import annotations

from typing import Any

from services.document_draft import DraftStore
from services.knowledge_claim_store import KnowledgeClaimStore
from services.skill_bank import SkillBank


async def project_status(
    claim_store: KnowledgeClaimStore | None = None,
    draft_store: DraftStore | None = None,
    bank: SkillBank | None = None,
) -> dict[str, Any]:
    """聚合各确定性 store 的项目运行态摘要。"""
    claims = await (claim_store or KnowledgeClaimStore()).list_claims()
    drafts = await (draft_store or DraftStore()).list_ids()
    bank = bank or SkillBank()
    return {
        "claim_count": len(claims),
        "draft_count": len(drafts),
        "active_skills": bank.active_versions(),
        "skill_candidate_count": sum(len(v) for v in bank.candidates().values()),
    }


def make_project_query_handler(
    claim_store: KnowledgeClaimStore | None = None,
    draft_store: DraftStore | None = None,
    bank: SkillBank | None = None,
) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return await project_status(claim_store, draft_store, bank)

    return handler


__all__ = ["make_project_query_handler", "project_status"]