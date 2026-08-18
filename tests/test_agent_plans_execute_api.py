"""Batch 5：/agent/plans/{plan_id}/execute 必须创建 PlanRun、经唯一 PlanExecutor +
真实 handler 逐步骤执行并返回 run_id——不再以 PENDING 伪装完成。

E2E：create -> submit(review) -> approve -> execute -> audit（terminal 终态可回放）。
"""
from __future__ import annotations

import httpx
import pytest

from api.deps import verify_token
from main import create_app


async def _approved_execute_plan(
    client: httpx.AsyncClient, *, tool: str, args: dict, actor: str = "author",
) -> dict:
    """create->submit->approve->execute 全流程，返回 execute 响应体。"""
    created = await client.post(
        "/api/v1/agent/plans",
        json={"goal": "量化一本书", "workspace_id": "ws", "identity": "agent-a",
              "steps": [{"tool": tool, "args": args}]},
    )
    assert created.status_code == 200
    plan_id = created.json()["data"]["plan_id"]
    version = created.json()["data"]["version"]

    sub = await client.post(
        f"/api/v1/agent/plans/{plan_id}/submit",
        json={"version": version, "actor": actor},
    )
    assert sub.status_code == 200

    appr = await client.post(
        f"/api/v1/agent/plans/{plan_id}/approve",
        json={"version": version, "actor": actor},
    )
    assert appr.status_code == 200

    executed = await client.post(
        f"/api/v1/agent/plans/{plan_id}/execute",
        json={"version": version, "actor": actor},
    )
    assert executed.status_code == 200
    body = executed.json()["data"]
    body["plan_id"] = plan_id
    return body


@pytest.mark.asyncio
async def test_execute_runs_real_handler_and_returns_run_id(monkeypatch):
    app = create_app()
    app.dependency_overrides[verify_token] = lambda: None

    async def _query(args: dict):
        return {"project_id": args["id"], "name": "样例项目"}

    # 宿主接线：唯一 PlanExecutor 消费真实 handler
    app.state.agent_tool_handlers = {"project.query": _query}

    async def _scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = await _approved_execute_plan(
                client, tool="project.query", args={"id": "p1"}
            )
            assert body["run_id"], "必须返回持久化 run_id"
            assert len(body["steps"]) == 1
            step = body["steps"][0]
            assert step["tool"] == "project.query"
            assert step["status"] == "OK", "真实 handler 必须执行并产出终态，而非 PENDING"
            assert step["result_available"] is True
            return body

    try:
        import asyncio

        body = await _scenario()
        plan_id = body["plan_id"]
        # audit：应含 completed/ok 终态（可回放），而非仅 dispatched/PENDING
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            audit = await client.get(f"/api/v1/agent/plans/{plan_id}/audit")
        events = audit.json()["data"]["events"]
        assert any(
            e.get("status") == "OK" for e in events
        ), "审计轨必须含终端 OK，而非仅有 PENDING/dispatched"
        assert not any(
            e.get("status") == "PENDING" and e.get("action") == "dispatched"
            for e in events
        ), "不允许仅 PENDING 伪装执行完成"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_execute_unapproved_rejected_403():
    app = create_app()
    app.dependency_overrides[verify_token] = lambda: None
    app.state.agent_tool_handlers = {}

    async def _scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/agent/plans",
                json={"goal": "x", "workspace_id": "ws", "identity": "agent-a",
                      "steps": [{"tool": "project.query", "args": {"id": "p1"}}]},
            )
            plan_id = created.json()["data"]["plan_id"]
            version = created.json()["data"]["version"]
            # 未批准直接执行 → 403（批准门 fail-closed）
            exec_resp = await client.post(
                f"/api/v1/agent/plans/{plan_id}/execute",
                json={"version": version, "actor": "author"},
            )
            assert exec_resp.status_code == 403

    try:
        await _scenario()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_replay_execute_idempotent_run_id_and_append_only_audit():
    """Batch 5：execute -> terminal -> replay——同级已批准版本重放执行返回同 run_id
    （确定性幂等），审计轨只追加增长且含 OK 终态（可回放）。"""
    app = create_app()
    app.dependency_overrides[verify_token] = lambda: None

    async def _query(args: dict):
        return {"project_id": args["id"]}

    app.state.agent_tool_handlers = {"project.query": _query}

    async def _scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/agent/plans",
                json={"goal": "g", "workspace_id": "ws", "identity": "agent-a",
                      "steps": [{"tool": "project.query", "args": {"id": "p1"}}]},
            )
            plan_id = created.json()["data"]["plan_id"]
            version = created.json()["data"]["version"]
            await client.post(f"/api/v1/agent/plans/{plan_id}/submit", json={"version": version, "actor": "author"})
            await client.post(f"/api/v1/agent/plans/{plan_id}/approve", json={"version": version, "actor": "author"})

            first = await client.post(f"/api/v1/agent/plans/{plan_id}/execute", json={"version": version, "actor": "author"})
            a1 = (await client.get(f"/api/v1/agent/plans/{plan_id}/audit")).json()["data"]["events"]

            # Batch 5：每步含机器可读 error_code（OK→OK）
            assert first.json()["data"]["steps"][0]["error_code"] == "OK"

            # 重放：同一已批准版本再次执行 → 同 run_id（确定性幂等）
            second = await client.post(f"/api/v1/agent/plans/{plan_id}/execute", json={"version": version, "actor": "author"})
            a2 = (await client.get(f"/api/v1/agent/plans/{plan_id}/audit")).json()["data"]["events"]

            assert first.json()["data"]["run_id"] == second.json()["data"]["run_id"]
            assert first.json()["data"]["steps"][0]["status"] == "OK"
            assert second.json()["data"]["steps"][0]["status"] == "OK"
            # 审计只追加增长，且始终含 OK 终态（可回放）
            assert len(a2) > len(a1)
            assert any(e["status"] == "OK" for e in a2)

    try:
        await _scenario()
    finally:
        app.dependency_overrides.clear()