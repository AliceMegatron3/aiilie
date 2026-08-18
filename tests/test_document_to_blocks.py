"""文档分段→原文块 黏合测试（V0.2：解析与分段 → 观点 → 证据 → 量化门）。"""
from __future__ import annotations

import pytest

from services.document_to_blocks import document_text, document_to_blocks
from services.evidence_binder import bind_evidence
from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_metrics import CandidateKnowledge
from services.parser import ParsedDocument, ParsedPassage


def _doc():
    return ParsedDocument(
        title="测试书",
        parser_id="builtin.txt",
        passages=[
            ParsedPassage(sequence=0, heading="第一章", text="科学方法强调因果推理需要可证伪的证据。"),
            ParsedPassage(sequence=1, heading="第二章", text="写作要把握读者情绪节奏。"),
            ParsedPassage(sequence=2, heading="", text="无关段落关于天气。"),
        ],
    )


def test_document_to_blocks_preserves_chapter_and_fills_empty_heading():
    doc = _doc()
    blocks = document_to_blocks(doc)
    assert blocks[0] == ("第一章", "科学方法强调因果推理需要可证伪的证据。")
    # 无标题段：用页/序号占位
    assert blocks[2][0].startswith("正文")
    assert "第一章" in document_text(doc)


@pytest.mark.asyncio
async def test_parser_doc_through_refinery_chain(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    blocks = document_to_blocks(_doc())
    refs = bind_evidence("因果推理需可证伪证据", blocks, document_id="book_x")
    assert refs and refs[0].chapter == "第一章"
    out = await store.add_candidate(
        CandidateKnowledge(claim="因果推理需可证伪证据", evidence=refs)
    )
    assert out.action in ("stored", "conflict", "duplicate")
    # 完全无共同词的观点 → 无证据 → 证据门拒
    refs_none = bind_evidence("股票均线金叉与成交量信号", blocks, document_id="book_x")
    assert refs_none == []
    out2 = await store.add_candidate(CandidateKnowledge(claim="股票均线金叉与成交量信号", evidence=refs_none))
    assert out2.action == "no_evidence"