"""PluginContext ↔ 知识主链路 接线测试（V0.5 submit_candidate → V0.2 门）。"""
from __future__ import annotations

import pytest

from services.knowledge_claim_store import KnowledgeClaimStore
from services.plugin_context import PluginContext
from services.plugin_context_wiring import make_claim_store_submit_candidate


@pytest.mark.asyncio
async def test_plugin_submit_candidate_with_evidence_stored(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    submit = make_claim_store_submit_candidate(store)
    ctx = PluginContext(
        identity="plug-x",
        project_scope="proj_a",
        submit_candidate=submit,
    )
    cand_id = await ctx.submit_candidate(
        {
            "claim": "因果推理需可证伪证据",
            "evidence": [{"document_id": "d1", "chapter": "c1", "quote": "因果推理需可证伪证据"}],
        }
    )
    assert cand_id.startswith("claim_")
    assert len(await store.list_claims()) == 1  # 入库


@pytest.mark.asyncio
async def test_plugin_submit_without_evidence_rejected(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    ctx = PluginContext(
        identity="plug-x", project_scope="proj_a",
        submit_candidate=make_claim_store_submit_candidate(store),
    )
    # 无证据 → no_evidence：返回空 id（不落库）
    cand_id = await ctx.submit_candidate({"claim": "无来源观点"})
    assert cand_id == ""
    assert len(await store.list_claims()) == 0


@pytest.mark.asyncio
async def test_plugin_submit_duplicate_returns_existing(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    submit = make_claim_store_submit_candidate(store)
    ctx = PluginContext(identity="plug-x", project_scope="proj_a", submit_candidate=submit)
    first = await ctx.submit_candidate(
        {"claim": "写作要把握情绪节奏", "evidence": [{"document_id": "d1", "quote": "写作要把握情绪节奏"}]}
    )
    dup = await ctx.submit_candidate(
        {"claim": "写作要把握情绪节奏", "evidence": [{"document_id": "d1", "quote": "写作要把握情绪节奏"}]}
    )
    assert dup == first
    assert len(await store.list_claims()) == 1