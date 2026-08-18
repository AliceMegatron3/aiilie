"""量化拆书 — 知识主张存储与去重/冲突门（V0.2：去重与冲突检测 + 可回放可解释）。

报告「三.2」：第一版量化只做确定性指标，作为辅助判断；「没有证据来源的内容不能直接
进入正式技能库」。本模块把 `knowledge_metrics` 的确定性指标变成实际的入库门：
- 新候选与已存主张比对：`claim_repetition_ratio >= duplicate_threshold`
  且无冲突 → 判定为重复（返回既有 claim），不重复入库；
- 存在冲突（`conflict_count>0`）→ 仍入库但标记 `flagged_conflict`，供作者审核；
- 否则正常存入，保留 候选+证据+指标+时间戳（可回放可解释）。

采用文件态持久化（safe_join 防标识符路径遍历），仿照 agent_plan_store。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.path_resolver import get_app_data_dir, safe_join
from services.knowledge_metrics import (
    CandidateKnowledge,
    ExistingClaim,
    compute_knowledge_metrics,
    dice_coefficient,
    metric_snapshot,
)

logger = logging.getLogger(__name__)

DEFAULT_DUPLICATE_THRESHOLD = 0.8


def _compute_input_hash(candidate: CandidateKnowledge) -> str:
    """对 claim+evidence 计算输入指纹（Batch 2：幂等/回放去重用）。"""
    parts = [candidate.claim, *(e.quote for e in candidate.evidence)]
    content = "\u0001".join(sorted(parts))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AddOutcome:
    action: str          # duplicate / stored / conflict
    candidate_id: str    # 新候选 id 或命中的既有 id
    metrics: dict[str, Any]
    existing_id: str = ""


class KnowledgeClaimStore:
    """确定性量化 + 去重/冲突门的知识主张存储。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.dir = base_dir or (get_app_data_dir() / "knowledge_claims")
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, candidate_id: str) -> Path:
        return safe_join(self.dir, f"{candidate_id}.json")

    async def add_candidate(
        self,
        candidate: CandidateKnowledge,
        *,
        duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
    ) -> AddOutcome:
        """入库门：比对既存主张，按重复/冲突/正常返回动作。

        报告「三.1」不变量：没有证据来源的内容不能进入正式技能库——
        无 evidence 的候选直接返回 `no_evidence`，不落库（fail-closed）。
        """
        if not (candidate.evidence or []):
            return AddOutcome(
                action="no_evidence",
                candidate_id="",
                metrics={"evidence_count": 0, "message": "缺少证据来源，拒绝入库"},
            )
        stored = await self.list_claims()
        existing: list[tuple[str, ExistingClaim]] = [
            (s["candidate_id"], ExistingClaim(
                claim=s["claim"],
                counterexamples=s["counterexamples"],
                rejected=s["metrics"].get("conflict_count", 0) > 0,
            ))
            for s in stored
        ]
        metrics = compute_knowledge_metrics(candidate, existing=[e for _, e in existing])

        repetition = float(metrics["claim_repetition_ratio"])
        conflicts = int(metrics["conflict_count"])
        if repetition >= duplicate_threshold and conflicts == 0:
            # 找相似度最高的既有主张
            best_id, best_sim = "", 0.0
            for cid, e in existing:
                sim = dice_coefficient(candidate.claim, e.claim)
                if sim > best_sim:
                    best_sim, best_id = sim, cid
            return AddOutcome(
                action="duplicate",
                candidate_id=best_id,
                existing_id=best_id,
                metrics=metrics,
            )

        # 否则入库；有冲突则标记供审核
        candidate_id = f"claim_{hashlib.sha256(candidate.claim.encode('utf-8')).hexdigest()[:16]}"
        record = {
            "candidate_id": candidate_id,
            "claim": candidate.claim,
            "evidence": [e.model_dump(mode="json") for e in candidate.evidence],
            "conditions": candidate.conditions,
            "counterexamples": candidate.counterexamples,
            "steps": candidate.steps,
            # Batch 2：显式持久化 provenance（可回放/可溯源到本次量化运行）
            "run_id": candidate.run_id,
            "source_document_id": candidate.source_document_id,
            "parser": candidate.parser,
            "model": candidate.model,
            "input_hash": candidate.input_hash or _compute_input_hash(candidate),
            "metrics": metric_snapshot(metrics),
            "flagged_conflict": conflicts > 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if await self._exists(candidate_id):
            return AddOutcome(action="duplicate", candidate_id=candidate_id, metrics=metrics)
        await asyncio.to_thread(self._path(candidate_id).write_text, json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        action = "conflict" if conflicts > 0 else "stored"
        return AddOutcome(action=action, candidate_id=candidate_id, metrics=metrics)

    async def list_claims(self) -> list[dict[str, Any]]:
        if not await asyncio.to_thread(self.dir.is_dir):
            return []
        claims: list[dict[str, Any]] = []
        for path in await asyncio.to_thread(lambda: list(self.dir.glob("*.json"))):
            try:
                data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
                claims.append(data)
            except Exception:  # noqa: BLE001
                logger.warning("忽略坏主张文件 %s", path.name)
        claims.sort(key=lambda c: c.get("candidate_id", ""))
        return claims

    async def _exists(self, candidate_id: str) -> bool:
        return await asyncio.to_thread(self._path(candidate_id).exists)

    async def delete(self, candidate_id: str) -> bool:
        path = self._path(candidate_id)
        if await asyncio.to_thread(path.exists):
            await asyncio.to_thread(path.unlink)
            return True
        return False


knowledge_claim_store = KnowledgeClaimStore()


__all__ = ["AddOutcome", "KnowledgeClaimStore", "knowledge_claim_store"]