"""文档草稿工具测试（V0.4：document.draft_create 真实 handler）。"""
from __future__ import annotations

import pytest

from services.agent_plan import AgentPlan, PlanStep
from services.document_draft import DraftStore, make_document_draft_handler, make_document_read_handler
from services.plan_executor import PlanExecutor


@pytest.mark.asyncio
async def test_draft_store_roundtrip(tmp_path):
    store = DraftStore(base_dir=tmp_path)
    draft = await store.create("proj_a", "正文草稿")
    loaded = await store.load(draft.draft_id)
    assert loaded is not None
    assert loaded.draft_id == draft.draft_id
    assert loaded.project_id == "proj_a"
    assert loaded.content == "正文草稿"
    assert loaded.status == "DRAFT"
    assert await store.load("missing") is None
    assert await store.delete(draft.draft_id) is True


@pytest.mark.asyncio
async def test_document_draft_create_runs_in_approved_plan(tmp_path):
    store = DraftStore(base_dir=tmp_path)
    plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="建草稿")
    v1 = plan.draft("建草稿", [PlanStep(tool="document.draft_create", args={"project_id": "proj_a", "content": "正文草稿内容"})])
    plan.submit_for_review(v1, "author")
    plan.approve(v1, "author")

    results = await PlanExecutor().execute_plan(
        plan, version=v1, actor="author",
        handlers={"document.draft_create": make_document_draft_handler(store)},
    )
    assert results[0].status == "OK"
    draft_id = results[0].result["draft_id"]
    loaded = await store.load(draft_id)
    assert loaded is not None and loaded.status == "DRAFT"


@pytest.mark.asyncio
async def test_document_create_then_read(tmp_path):
    store = DraftStore(base_dir=tmp_path)
    handler_create = make_document_draft_handler(store)
    handler_read = make_document_read_handler(store)
    r1 = await handler_create({"project_id": "p1", "content": "正文A"})
    r2 = await handler_read({"draft_id": r1["draft_id"]})
    assert r2["content"] == "正文A"
    assert r2["status"] == "DRAFT"
    # 缺 id 或不存在 → 拒绝
    with pytest.raises(ValueError):
        await handler_read({"draft_id": "missing"})