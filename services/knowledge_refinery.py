"""量化拆书 — 知识炼制流水线（V0.2 确定性主链路编排）。

方向报告「三、目标架构」把主链路定义为：
    解析与分段 → 观点/概念提取 → 证据绑定 → 量化评估 → 冲突检测与去重
    → 技能候选 → 作者审核 → 激活可用技能

本模块把已实现的确定性构件串成一条可编排流水线（不依赖 LLM/DB）：
    bind_evidence（段落→证据）→ compute/claim_store 门（量化+去重+冲突+证据）
    → 通过且无冲突的主张 → SkillVersionedSkill 提出 `@minor+1.0-candidate`。
输出每条的 action 与候选版本号，保留证据与指标（可回放可解释）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from services.document_to_blocks import document_to_blocks
from services.evidence_binder import bind_evidence
from services.knowledge_claim_store import KnowledgeClaimStore, knowledge_claim_store
from services.knowledge_metrics import CandidateKnowledge
from services.skill_evaluator import run_smoke_tests
from services.skill_versioning import SkillVersionedSkill


logger = logging.getLogger(__name__)


@dataclass
class ClaimResult:
    claim: str
    action: str            # no_evidence / duplicate / conflict / stored
    candidate_id: str = ""
    existing_id: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    skill_candidate: str = ""   # e.g. skill.foo@1.3.0-candidate（仅 stored 且无冲突时提出）
    evidence_count: int = 0
    tests_passed: bool = False  # 冒烟测试是否过测（激活仍待作者审核）
    governance_candidate_id: str = ""  # SkillGovernance 缓冲池候选 id（Batch 2 闭环）


@dataclass
class RefineryReport:
    total: int = 0
    stored: int = 0
    duplicates: int = 0
    conflicts: int = 0
    no_evidence: int = 0
    proposed_candidates: list[str] = field(default_factory=list)
    details: list[ClaimResult] = field(default_factory=list)
    # Batch 2：整体状态——COMPLETED/NEEDS_REVIEW/FAILED（不得把无证据/失败伪装为成功）
    status: str = "COMPLETED"


async def refine(
    blocks: Iterable[str],
    claims: Iterable[str | dict[str, Any]],
    *,
    document_id: str,
    claim_store: KnowledgeClaimStore | None = None,
    skills: dict[str, SkillVersionedSkill] | None = None,
    skill_name: str = "skill.extracted",
    run_id: str = "",
    parser: str = "",
    model: str = "",
    governance: Any | None = None,
) -> RefineryReport:
    """跑一遍确定性炼制主链路，返回逐条 action 与技能候选版本。

    claims 支持纯字符串，或 dict{claim, conditions, counterexamples, steps,
    run_id, source_document_id, parser, model}，以便把反例/适用条件带入去重与
    冲突判定，并把 provenance 显式落到候选记录（Batch 2，非 extra=allow 透传）。

    `governance` 为可选的 SkillGovernance 实例：当一条主张「已入库且有证据、且冒烟
    过测」时，调用其 `submit_candidate`（内容哈希幂等的唯一注册入口），把该主张注册进
    技能治理缓冲池，返回的 candidate_id 记录在 ClaimResult.governance_candidate_id。
    缺省 None 时跳过注册（保持本模块纯确定性、便于离线单测）。
    """
    store = claim_store or knowledge_claim_store
    report = RefineryReport()

    for item in claims:
        if isinstance(item, dict):
            claim = str(item.get("claim", "")).strip()
            conditions = item.get("conditions", []) or []
            counterexamples = item.get("counterexamples", []) or []
            steps = item.get("steps", []) or []
            item_run = item.get("run_id") or run_id
            item_source = item.get("source_document_id") or document_id
            item_parser = item.get("parser") or parser
            item_model = item.get("model") or model
        else:
            claim = str(item).strip()
            conditions, counterexamples, steps = [], [], []
            item_run, item_source, item_parser, item_model = run_id, document_id, parser, model
        if not claim:
            continue
        refs = bind_evidence(claim, blocks, document_id=document_id)
        outcome = await store.add_candidate(
            CandidateKnowledge(
                claim=claim,
                evidence=refs,
                conditions=conditions,
                counterexamples=counterexamples,
                steps=steps,
                run_id=item_run,
                source_document_id=item_source,
                parser=item_parser,
                model=item_model,
            )
        )

        result = ClaimResult(
            claim=claim,
            action=outcome.action,
            candidate_id=outcome.candidate_id,
            existing_id=outcome.existing_id,
            metrics=outcome.metrics,
            evidence_count=len(refs),
        )
        report.total += 1

        if outcome.action == "no_evidence":
            report.no_evidence += 1
        elif outcome.action == "duplicate":
            report.duplicates += 1
        elif outcome.action == "conflict":
            report.conflicts += 1
        elif outcome.action == "stored":
            report.stored += 1
            # 通过且无冲突 → 提出技能候选版本（不允许覆盖激活版）
            skill = (skills or {}).get(skill_name, SkillVersionedSkill(skill_name, "1.0.0"))
            candidate_label = skill.propose_candidate("minor")
            result.skill_candidate = candidate_label
            report.proposed_candidates.append(candidate_label)
            # 候选 → 压力测试：冒烟过测才标记"已过测"（激活仍待作者审核，不在此自动激活）
            smoke = run_smoke_tests(claim=claim, evidence_count=len(refs),
                                    conditions=conditions, steps=steps)
            skill.record_tests(candidate_label, smoke.ok())
            result.tests_passed = smoke.ok()
            # Batch 2 闭环：有证据且过测 → 注册进 SkillGovernance 缓冲池（唯一注册入口）
            if governance is not None and result.tests_passed:
                try:
                    result.governance_candidate_id = governance.submit_candidate(
                        name=skill_name,
                        prompt=(
                            f"{claim}\n[证据数={len(refs)} 来源={item_source} "
                            f"run={item_run} 解析={item_parser} 模型={item_model}]"
                        ),
                        confidence=float(outcome.metrics.get("rule_confidence", 0.0)),
                        source_task=item_run,
                        artifact={
                            "claim_candidate_id": outcome.candidate_id,
                            "source_document_id": item_source,
                            "run_id": item_run,
                            "parser": item_parser,
                            "model": item_model,
                            "evidence_count": len(refs),
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    # 治理注册失败不应中断主链路；保留空 id，由上层审计。
                    result.governance_candidate_id = ""
                    logger.warning("[Refinery] SkillGovernance 注册失败 claim=%s: %s", claim, exc)

        report.details.append(result)

    # Batch 2：显式状态（空文档/无证据/冲突 → FAILED/NEEDS_REVIEW，不伪装成功）
    blocks_list = [b for b in blocks if str(b).strip()]
    if not blocks_list:
        report.status = "FAILED"  # 空文档：无任何源内容可炼制
    elif report.no_evidence > 0 or report.conflicts > 0:
        report.status = "NEEDS_REVIEW"
    else:
        report.status = "COMPLETED"
    return report


async def refine_document(
    document: Any,
    claims: Iterable[str | dict[str, Any]],
    *,
    claim_store: KnowledgeClaimStore | None = None,
    skills: dict[str, SkillVersionedSkill] | None = None,
    skill_name: str = "skill.extracted",
) -> RefineryReport:
    """高层入口：解析文档（ParsedDocument）→ 分段为 (chapter,text) 块 → 跑主链路。"""
    blocks = document_to_blocks(document)
    return await refine(
        blocks,
        claims,
        document_id=getattr(document, "title", "") or "doc",
        claim_store=claim_store,
        skills=skills,
        skill_name=skill_name,
    )


__all__ = ["ClaimResult", "RefineryReport", "refine", "refine_document"]