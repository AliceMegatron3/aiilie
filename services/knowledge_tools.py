"""受控执行 — 知识域默认 tool handlers（V0.4 第一批工具真实实现）。

提供可直接注入 `PlanExecutor`/`PluginContext` 的默认 handler，作用于
`KnowledgeClaimStore`（确定性、非 LLM）：
- `library.search_cards`   按词袋相似检索已入库主张（查书卡）
- `knowledge.consistency_check`  知识一致性检查（冲突/近义/来源覆盖）

`make_default_knowledge_handlers(store)` 返回白名单工具 → handler 的注册表，
便于 PlanExecutor 一次注入多个受控工具。
"""
from __future__ import annotations

from typing import Any

from services.knowledge_claim_store import KnowledgeClaimStore, knowledge_claim_store
from services.knowledge_consistency import make_consistency_handler
from services.knowledge_metrics import dice_coefficient


async def search_claims(
    query: str, limit: int = 8, store: KnowledgeClaimStore | None = None
) -> list[dict[str, Any]]:
    store = store or knowledge_claim_store
    claims = await store.list_claims()
    scored = [
        (dice_coefficient(query, c["claim"]), c)
        for c in claims
        if dice_coefficient(query, c["claim"]) > 0
    ]
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:max(1, limit)]]


def make_search_cards_handler(store: KnowledgeClaimStore | None = None) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", ""))
        limit = int(args.get("limit", 8))
        hits = await search_claims(query, limit, store)
        return {"hits": hits, "count": len(hits)}

    return handler


def make_default_knowledge_handlers(
    store: KnowledgeClaimStore | None = None,
    skills: dict[str, Any] | None = None,
    skill_name: str = "skill.extracted",
) -> dict[str, Any]:
    """白名单工具 → handler 的默认注册表（供 PlanExecutor 一次注入）。

    含 `library.quantize_start`（发起量化主链路），与既有检索/一致性工具一并注入。
    """
    return {
        "library.search_cards": make_search_cards_handler(store),
        "knowledge.consistency_check": make_consistency_handler(store),
        "library.quantize_start": make_quantize_start_handler(store, skills, skill_name),
    }


def make_quantize_start_handler(
    store: KnowledgeClaimStore | None = None,
    skills: dict[str, Any] | None = None,
    skill_name: str = "skill.extracted",
) -> Any:
    """`library.quantize_start`：发起量化——跑确定性量化主链路（refine），
    返回 各主张 action / 证据数 / 量化指标 / 提出候选（不含反思评审，评审在反思阶段）。"""
    from services.knowledge_refinery import refine

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        blocks = args.get("blocks") or []
        claims = args.get("claims") or []
        document_id = str(args.get("document_id", "") or "doc")
        if not blocks or not claims:
            raise ValueError("library.quantize_start 需要 blocks 与 claims")
        report = await refine(
            blocks, claims, document_id=document_id,
            claim_store=store, skills=skills, skill_name=skill_name,
        )
        return {
            "total": report.total,
            "stored": report.stored,
            "duplicates": report.duplicates,
            "conflicts": report.conflicts,
            "no_evidence": report.no_evidence,
            "proposed": report.proposed_candidates,
            "details": [
                {
                    "claim": d.claim, "action": d.action,
                    "evidence_count": d.evidence_count,
                    "metrics": d.metrics,
                    "skill_candidate": d.skill_candidate,
                    "tests_passed": d.tests_passed,
                }
                for d in report.details
            ],
        }

    return handler


__all__ = [
    "make_default_knowledge_handlers",
    "make_quantize_start_handler",
    "make_search_cards_handler",
    "search_claims",
]