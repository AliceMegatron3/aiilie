"""量化拆书 — 知识一致性检查（V0.4 第一批工具：knowledge.consistency_check）。

方向报告「四、第一批工具」含"运行知识一致性检查"。本模块基于
`KnowledgeClaimStore`（确定性指标 + 去重/冲突门）扫描已入库主张，给出：
- 冲突数（含 `flagged_conflict`）
- 重复/近义对数（词袋 Dice 相似）
- 独立来源及其覆盖率
- 无证据标注条数（不应 >0，证据门已挡）

并暴露 `make_consistency_handler()` 作为 `PlanExecutor` 可用的真实默认 handler
（参数默认空 dict，返回结构化报告）。
"""
from __future__ import annotations

from typing import Any

from services.knowledge_claim_store import KnowledgeClaimStore, knowledge_claim_store
from services.knowledge_metrics import dice_coefficient


async def run_consistency_check(
    store: KnowledgeClaimStore | None = None,
) -> dict[str, Any]:
    """扫描主张库并返回一致性报告（规则结果，非 LLM 裁决）。"""
    store = store or knowledge_claim_store
    claims = await store.list_claims()

    total = len(claims)
    flagged_conflicts = [c for c in claims if c.get("flagged_conflict")]
    no_evidence = [c for c in claims if not c.get("evidence")]

    sources: set[str] = set()
    for c in claims:
        for ev in c.get("evidence") or []:
            if isinstance(ev, dict) and ev.get("document_id"):
                sources.add(str(ev["document_id"]))

    near_dups: set[tuple[str, str]] = set()
    for i in range(total):
        for j in range(i + 1, total):
            a, b = claims[i], claims[j]
            if dice_coefficient(a["claim"], b["claim"]) >= 0.8:
                key = tuple(sorted((a["candidate_id"], b["candidate_id"])))
                near_dups.add(key)

    return {
        "total_claims": total,
        "conflict_count": len(flagged_conflicts),
        "near_duplicate_pairs": len(near_dups),
        "independent_sources": len(sources),
        "source_coverage": round(len(sources) / total, 4) if total else 0.0,
        "no_evidence_count": len(no_evidence),
        "consistent": (not flagged_conflicts) and (not near_dups) and (not no_evidence),
    }


def make_consistency_handler(store: KnowledgeClaimStore | None = None) -> Any:
    """构造 `knowledge.consistency_check` 工具的默认 handler（供 PlanExecutor 注入）。"""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return await run_consistency_check(store)

    return handler


__all__ = ["make_consistency_handler", "run_consistency_check"]