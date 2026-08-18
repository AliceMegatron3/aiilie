"""受控执行 — 反思域默认 tool handlers（V0.4 第一批工具真实实现）。

- `reflection.view_candidates`  查看候选中技能版本（基于 `SkillBank`）
- `reflection.view_active`      查看已激活技能版本（只读，随附）
- `reflection.start`            启动一站式确定性反思（炼流水线 → 反思评审），
                                返回各候选的入库动作与评审门结果
"""
from __future__ import annotations

from typing import Any

from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_refinery import refine
from services.reflection_critique import critique_from_refine_detail
from services.skill_bank import SkillBank
from services.skill_versioning import SkillVersionedSkill


def make_view_candidates_handler(bank: SkillBank | None = None) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        bank_ref = bank or SkillBank()
        return {"candidates": bank_ref.candidates()}

    return handler


def make_view_active_handler(bank: SkillBank | None = None) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        bank_ref = bank or SkillBank()
        return {"active": bank_ref.active_versions()}

    return handler


def make_reflection_start_handler(
    store: KnowledgeClaimStore | None = None,
    skills: dict[str, SkillVersionedSkill] | None = None,
    skill_name: str = "skill.extracted",
) -> Any:
    """`reflection.start`：跑一趟确定性反思主链路（refine → 反思评审）。"""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        blocks = args.get("blocks") or []
        claims = args.get("claims") or []
        document_id = str(args.get("document_id", "") or "doc")
        if not blocks or not claims:
            raise ValueError("reflection.start 需要 blocks 与 claims")
        report = await refine(
            blocks, claims, document_id=document_id,
            claim_store=store, skills=skills, skill_name=skill_name,
        )
        return {
            "total": report.total,
            "stored": report.stored,
            "duplicates": report.duplicates,
            "conflicts": report.conflicts,
            "proposed": report.proposed_candidates,
            "critique": [
                critique_from_refine_detail(d).to_dict()
                for d in report.details if d.action == "stored"
            ],
        }

    return handler


def make_default_reflector_handlers(
    bank: SkillBank | None = None,
    store: KnowledgeClaimStore | None = None,
    skills: dict[str, SkillVersionedSkill] | None = None,
    skill_name: str = "skill.extracted",
) -> dict[str, Any]:
    return {
        "reflection.view_candidates": make_view_candidates_handler(bank),
        "reflection.view_active": make_view_active_handler(bank),
        "reflection.start": make_reflection_start_handler(store, skills, skill_name),
    }


__all__ = [
    "make_default_reflector_handlers",
    "make_reflection_start_handler",
    "make_view_active_handler",
    "make_view_candidates_handler",
]