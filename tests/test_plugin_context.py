"""PluginContext 测试（V0.5 插件 SDK 受限访问面）。"""
from __future__ import annotations

import pytest

from services.plugin_context import (
    ContextDeniedError,
    PluginContext,
    ResourceBudget,
    assert_no_privileged_surface,
)


def test_no_privileged_surface_exposed():
    ctx = PluginContext(identity="plug-x", project_scope="proj_a")
    assert assert_no_privileged_surface(ctx) == []


def test_read_document_respects_project_scope():
    captured = {}

    async def fake_read(project_id, doc_id):
        captured["hit"] = (project_id, doc_id)
        return {"doc_id": doc_id}

    ctx = PluginContext(
        identity="plug-x",
        project_scope="proj_a",
        read_doc=fake_read,
    )

    async def run():
        # 作用域内：放行
        doc = await ctx.read_document("proj_a", "doc_1")
        assert doc == {"doc_id": "doc_1"}
        # 越权访问其它项目：拒绝（fail-closed）
        with pytest.raises(ContextDeniedError):
            await ctx.read_document("proj_b", "doc_1")

    _sync(run())


def test_submit_candidate_never_activates():
    async def fake_submit(candidate):
        # 只入候选池（PENDING），绝不直接激活正式技能
        return "skillcand_abc"

    ctx = PluginContext(
        identity="plug-x", project_scope="proj_a", submit_candidate=fake_submit
    )

    async def run():
        cand_id = await ctx.submit_candidate({"claim": "观点", "evidence": []})
        assert cand_id.startswith("skillcand_")

    _sync(run())


def test_budget_exposed_as_dict():
    b = ResourceBudget(timeout_seconds=15, max_input_bytes=1024, max_output_bytes=2048)
    ctx = PluginContext(identity="x", project_scope=None, budget=b)
    d = ctx.resource_budget.as_dict()
    assert d["timeout_seconds"] == 15
    assert d["max_input_bytes"] == 1024
    assert d["max_output_bytes"] == 2048


def test_unwired_capability_denied_not_crash():
    ctx = PluginContext(identity="x", project_scope=None)

    async def run():
        with pytest.raises(ContextDeniedError):
            await ctx.read_document("p", "d")
        with pytest.raises(ContextDeniedError):
            await ctx.search_resources("q")

    _sync(run())


def test_audit_sink_invoked_on_behalf_of_plugin():
    events = []

    async def fake_audit(ev):
        events.append(ev)

    ctx = PluginContext(identity="plug-x", project_scope=None, audit=fake_audit)

    async def run():
        await ctx.emit_audit_event({"action": "query", "identity": ctx.identity})

    _sync(run())
    assert events and events[0]["identity"] == "plug-x"


def _sync(coro):
    import asyncio

    asyncio.run(coro)