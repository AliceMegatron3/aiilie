"""确定性量化指标（V0.2 量化拆书 — 第一版，不依赖任何 LLM）。

方向报告「三.2 量化阶段」：第一版只做确定性指标，规则计算结果作为辅助判断
持久化，LLM 评分仅可作为建议、不作为最终裁决。

本模块把一条“候选知识”（claim + evidence[] + conditions + counterexamples +
steps）与已有技能/主张语料做规则级比对，产出一组可解释的确定性指标：

    evidence_count                 证据数量
    independent_chapter_count      独立章节数量（document_id, chapter 去重）
    source_coverage                来源覆盖率（独立文档/证据总数）
    claim_repetition_ratio         观点重复度（与已有主张的最大词袋 Dice 相似度）
    conflict_count                 观点冲突数（相似且被判定 rejected / 共享反例的已有主张）
    condition_completeness         条件完整度（必备条件槽被候选条件覆盖的比例）
    counterexample_count           反例数量
    executable_step_count          可执行步骤数量
    max_similarity_to_existing     与现有技能/主张的最大语义相似度

说明：所有指标均为字符串/数量层面的规则计算，证据原文与规则结果一并保留，
不伪造“绝对真理”式评分。
"""
from __future__ import annotations

import re
import string
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Iterable

from pydantic import BaseModel, Field

# 归一化切词（中文按字-词、英文按小写单词），供 Dice 相似度使用
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """切词：英文单词小写、中文按单字 + 相邻双字捕获，粗粒度即可。"""
    parts = _TOKEN_RE.findall(text or "")
    tokens: set[str] = set()
    for p in parts:
        if re.fullmatch(r"[a-zA-Z]+", p):
            tokens.add(p.lower())
            continue
        # 中文：加入单字与长度 2 的窗口滑片，提升中文观点相似度召回
        for ch in p:
            tokens.add(ch)
        for i in range(len(p) - 1):
            tokens.add(p[i : i + 2])
    return tokens


def dice_coefficient(a: str, b: str, min_ratio: float = 0.0) -> float:
    """词袋 Dice 系数（0..1）。同时用 SequenceMatcher 兜底，兼顾无序与顺序相似。"""
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    dice = 2.0 * overlap / (len(ta) + len(tb)) if (ta or tb) else 0.0
    if min_ratio > 0 and dice < min_ratio:
        # 词袋重叠过弱时，退回顺序相似度（子串/编辑近似），避免漏检重复释义
        seq = SequenceMatcher(None, a, b).ratio()
        return min(dice, seq) if dice < 0.5 else dice
    return dice


class EvidenceRef(BaseModel):
    document_id: str
    chapter: str = ""
    location: str = ""
    quote: str = ""


class MetricSnapshot(BaseModel):
    """确定性量化指标快照（Batch 2：把规则结果以显式、可回放的结构落盘）。

    全部字段为确定性规则计算产物，不伪装 LLM 裁决；`created_at` 记录快照时刻，
    `via` 固定为规则计算，避免外部误judgment。与 compute_knowledge_metrics 的
    返回值键一一对应，供上层以强类型方式读取（不用裸 dict 漂移）。
    """

    evidence_count: int = 0
    independent_chapter_count: int = 0
    source_coverage: float = 0.0
    claim_repetition_ratio: float = 0.0
    conflict_count: int = 0
    condition_completeness: float = 0.0
    counterexample_count: int = 0
    executable_step_count: int = 0
    max_similarity_to_existing: float = 0.0
    rule_confidence: float = 0.0
    via: str = "rule"  # 计算来源：仅确定性规则
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_metrics(cls, metrics: dict[str, Any]) -> "MetricSnapshot":
        """由 compute_knowledge_metrics 的 dict 结果构造强类型快照。"""
        known = {k: v for k, v in metrics.items() if k in cls.model_fields}
        return cls(**known)


def metric_snapshot(metrics: dict[str, Any]) -> dict[str, Any]:
    """返回可持久化/可回放的指标快照 dict（含可靠字段与时间戳）。"""
    return MetricSnapshot.from_metrics(metrics).model_dump(mode="json")


class CandidateKnowledge(BaseModel):
    """一条候选知识（报告三.1：没有证据来源的内容不能直接进入正式技能库）。

    Batch 2 增加 provenance 字段（非 extra=allow 透传，均为显式建模）：
      run_id / source_document_id / parser / model / input_hash，
    保证每条候选可回放、可溯源到「本次量化运行 + 源文档 + 解析/模型 + 输入指纹」。
    """

    claim: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # ── provenance（Batch 2） ──
    run_id: str = ""
    source_document_id: str = ""
    parser: str = ""
    model: str = ""
    input_hash: str = ""


class ExistingClaim(BaseModel):
    """已有主张/技能（用于重复度与冲突比对）。

    rejected=True 表示该主张此前被作者驳回或经反例证伪，用于“冲突数”计算。
    """

    claim: str
    counterexamples: list[str] = Field(default_factory=list)
    rejected: bool = False


# 建议的条件槽关键字（用于“条件完整度”启发式）：命中越多认为条件越完备
DEFAULT_REQUIRED_CONDITION_KEYS = (
    "when",
    "when条件",
    "where",
    "场景",
    "who",
    "对象",
    "under",
    "在…",
    "if",
    "如果",
    "amount",
    "程度",
    "boundary",
    "边界",
    "例外",
    "except",
)


def _condition_keyword_hits(conditions: Iterable[str], keys: Iterable[str]) -> int:
    text = " ".join(conditions or [])
    return sum(1 for key in keys if key in text)


def _condition_coverage(conditions: Iterable[str], keys: Iterable[str]) -> float:
    keys = list(keys)
    if not keys:
        return 0.0
    return _condition_keyword_hits(conditions, keys) / len(keys)


def compute_knowledge_metrics(
    candidate: CandidateKnowledge,
    *,
    existing: Iterable[ExistingClaim] | None = None,
    required_condition_keys: Iterable[str] = DEFAULT_REQUIRED_CONDITION_KEYS,
    conflict_threshold: float = 0.6,
) -> dict[str, float | int]:
    """对候选知识计算一组确定性量化指标。

    参数：
        candidate                  候选知识（claim/evidence/conditions/...）
        existing                   已有主张/技能语料（缺省视为空）
        required_condition_keys    条件完整度参照的关键字槽
        conflict_threshold         Dice 相似度阈值：达到即视为“近似”，
                                   若该已有主张还被判定 rejected 或共享反例，则计入冲突。
    """
    evidence = candidate.evidence or []

    # —— 确定性结构指标 ——
    evidence_count = len(evidence)
    independent_chapter_count = len(
        {(e.document_id, e.chapter or "") for e in evidence}
    )
    distinct_docs = len({e.document_id for e in evidence})
    source_coverage = distinct_docs / evidence_count if evidence_count else 0.0
    counterexample_count = len(candidate.counterexamples)
    executable_step_count = len(candidate.steps)

    # —— 与已有语料的重复度 / 冲突 / 相似度 ——
    existing_list = list(existing or [])
    similarities = [dice_coefficient(candidate.claim, e.claim) for e in existing_list]
    max_similarity = max(similarities) if similarities else 0.0
    repetition_ratio = max_similarity  # 观点重复度取最大相似度

    my_counterexamples = {_norm(c) for c in candidate.counterexamples}
    conflict_count = 0
    for e_item, sim in zip(existing_list, similarities):
        if sim < conflict_threshold:
            continue
        shared_example = bool(
            my_counterexamples and my_counterexamples & {_norm(c) for c in e_item.counterexamples}
        )
        if (e_item.rejected or shared_example) and sim >= conflict_threshold:
            conflict_count += 1

    # —— 条件完整度（确定性启发式）——
    condition_completeness = _condition_coverage(
        candidate.conditions, required_condition_keys
    )

    # 置信度：规则证据密度（0..1），仅作为辅助，不伪装绝对真理
    rule_confidence = min(1.0, evidence_count / 5.0) * 0.6 + condition_completeness * 0.4

    return {
        "evidence_count": evidence_count,
        "independent_chapter_count": independent_chapter_count,
        "source_coverage": round(source_coverage, 4),
        "claim_repetition_ratio": round(repetition_ratio, 4),
        "conflict_count": conflict_count,
        "condition_completeness": round(condition_completeness, 4),
        "counterexample_count": counterexample_count,
        "executable_step_count": executable_step_count,
        "max_similarity_to_existing": round(max_similarity, 4),
        "rule_confidence": round(rule_confidence, 4),
    }


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").strip().lower() if ch not in string.punctuation + " \t")


__all__ = [
    "CandidateKnowledge",
    "EvidenceRef",
    "ExistingClaim",
    "compute_knowledge_metrics",
    "dice_coefficient",
]