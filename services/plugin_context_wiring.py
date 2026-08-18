"""可扩展插件内核 ↔ 量化拆书 接线（V0.5 `submit_candidate` → V0.2 知识主链路）。

方向报告「五.5」：`PluginContext.submit_candidate()` 是插件唯一可提交候选的入口。
本模块把该入口接到 `KnowledgeClaimStore.add_candidate`（证据门 + 去重/冲突门，
确定性、非 LLM），使插件提交的候选与作者知识走同一道门槛：
- 无证据 → `no_evidence`（拒绝）；
- 重复/冲突会被如实返回，绝不自动激活正式技能（激活仍待作者审核）。
"""
from __future__ import annotations

from typing import Any

from services.knowledge_claim_store import KnowledgeClaimStore, knowledge_claim_store
from services.knowledge_metrics import CandidateKnowledge, EvidenceRef
from services.plugin_context import ContextDeniedError


def make_claim_store_submit_candidate(
    store: KnowledgeClaimStore | None = None,
    duplicate_threshold: float = 0.8,
) -> Any:
    """构造可注入 `PluginContext` 的 submit_candidate 回调（基于确定性知识主链路）。"""
    store = store or knowledge_claim_store

    async def submit_candidate(candidate: dict[str, Any]) -> str:
        if not isinstance(candidate, dict) or not candidate.get("claim"):
            raise ContextDeniedError("候选必须包含非空 claim")
        claim = str(candidate["claim"])
        evidence = [
            EvidenceRef(
                document_id=str(e.get("document_id", "")),
                chapter=str(e.get("chapter", "")),
                location=str(e.get("location", "")),
                quote=str(e.get("quote", "")),
            )
            for e in (candidate.get("evidence") or [])
        ]
        outcome = await store.add_candidate(
            CandidateKnowledge(
                claim=claim,
                evidence=evidence,
                conditions=[str(c) for c in (candidate.get("conditions") or [])],
                counterexamples=[str(c) for c in (candidate.get("counterexamples") or [])],
                steps=[str(s) for s in (candidate.get("steps") or [])],
            ),
            duplicate_threshold=duplicate_threshold,
        )
        # 仅返回候选 id；激活与否由作者审核决定，此处绝不激活
        return outcome.candidate_id

    return submit_candidate


__all__ = ["make_claim_store_submit_candidate"]