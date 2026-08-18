"""量化拆书 — 证据绑定（V0.2 主链路「段落→观点→证据」）。

方向报告「三.1 提取阶段」：每条候选知识至少带 evidence[]（document_id/chapter/
location/quote）。本模块提供**确定性证据绑定**：
- 在章节/段落中按词袋重叠为观点匹配最相关的原文片段，产出 `EvidenceRef`；
- 无匹配时返回空（后续 `KnowledgeClaimStore` 的证据门将拒绝无来源候选）——
  天然满足「没有证据来源的内容不能直接进入正式技能库」。
"""
from __future__ import annotations

import re
from typing import Iterable

from services.knowledge_metrics import EvidenceRef

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
MAX_QUOTE_LENGTH = 200


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for p in _TOKEN_RE.findall(text or ""):
        if re.fullmatch(r"[a-zA-Z]+", p):
            tokens.add(p.lower())
            continue
        for ch in p:
            tokens.add(ch)
        for i in range(len(p) - 1):
            tokens.add(p[i : i + 2])
    return tokens


def bind_evidence(
    claim: str,
    blocks: Iterable[str],
    *,
    document_id: str,
    chapter: str | None = None,
    max_refs: int = 3,
    min_overlap: int = 1,
) -> list[EvidenceRef]:
    """按词袋重叠为观点匹配最相关段落，产出带来源锚点的证据。

    blocks: 每个元素为一段原文（可为 (chapter, text) 元组或纯字符串）。
    返回按相关度降序的证据列表；无匹配则空列表。
    """
    claim_tokens = _tokenize(claim)
    if not claim_tokens:
        return []

    scored: list[tuple[int, int, str, str]] = []  # (overlap, ordinal, chapter, text)
    for idx, block in enumerate(blocks):
        if isinstance(block, tuple):
            chap, text = block[0], block[1]
        else:
            chap, text = chapter, block
        text = (text or "").strip()
        if not text:
            continue
        overlap = len(claim_tokens & _tokenize(text))
        if overlap >= min_overlap:
            scored.append((overlap, idx, chap or "", text))

    scored.sort(key=lambda x: (-x[0], x[1]))
    refs: list[EvidenceRef] = []
    for _, ordinal, chap, text in scored[:max_refs]:
        refs.append(
            EvidenceRef(
                document_id=document_id,
                chapter=chap,
                location=f"para#{ordinal}",
                quote=text[:MAX_QUOTE_LENGTH],
            )
        )
    return refs


__all__ = ["bind_evidence"]