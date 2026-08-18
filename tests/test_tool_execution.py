"""白名单工具执行门测试（V0.4：权限/参数/幂等/超时/审计）。"""
from __future__ import annotations

import asyncio
import pytest

from services.tool_execution import IdempotentToolRunner, RunResult
from services.whitelisted_tools import PermissionLevel


@pytest.mark.asyncio
async def test_denies_unregistered_or_low_permission():
    runner = IdempotentToolRunner()

    async def handler(args):
        return "ok"

    den = await runner.run("evil.tool", {"a": 1}, actor="author", permission=PermissionLevel.GOVERN, handler=handler)
    assert den.status == "DENIED"
    low = await runner.run("library.quantize_start", {"a": 1}, actor="x", permission=PermissionLevel.READ, handler=handler)
    assert low.status == "DENIED"


@pytest.mark.asyncio
async def test_invalid_args_rejected():
    runner = IdempotentToolRunner()

    async def handler(args):
        return "ok"

    # 非 dict → INVALID；空 dict 视为合法的空参工具
    res = await runner.run("project.query", ["not", "dict"], actor="a", permission=PermissionLevel.GOVERN, handler=handler)
    assert res.status == "INVALID"
    ok_res = await runner.run("project.query", {}, actor="a", permission=PermissionLevel.GOVERN, handler=handler)
    assert ok_res.status == "OK"


@pytest.mark.asyncio
async def test_ok_status_and_audit_terminal():
    events = []

    async def handler(args):
        return {"value": args["n"] * 2}

    async def audit(ev):
        events.append(ev)

    runner = IdempotentToolRunner(audit=audit)
    res = await runner.run("project.query", {"n": 2}, actor="author", permission=PermissionLevel.GOVERN, handler=handler)
    assert res.status == "OK"
    assert res.result == {"value": 4}
    # 开始 + 成功终态都已审计回流
    assert {e["action"] for e in events} == {"start", "ok"}


@pytest.mark.asyncio
async def test_idempotent_running_returns_running():
    release = asyncio.Event()

    async def handler(args):
        await release.wait()
        return "done"

    runner = IdempotentToolRunner(timeout_seconds=30)
    task1 = asyncio.create_task(
        runner.run("project.query", {"q": "x"}, actor="a", permission=PermissionLevel.GOVERN, handler=handler)
    )
    await asyncio.sleep(0.05)
    # 同参数重复发起 → RUNNING（幂等）
    res2 = await runner.run("project.query", {"q": "x"}, actor="a", permission=PermissionLevel.GOVERN, handler=handler)
    assert res2.status == "RUNNING"
    assert res2.idempotent is True
    release.set()
    r1 = await task1
    assert r1.status == "OK"


@pytest.mark.asyncio
async def test_timeout_marks_error_and_audits():
    events = []

    async def never(args):
        await asyncio.sleep(10)

    async def audit(ev):
        events.append(ev)

    runner = IdempotentToolRunner(timeout_seconds=1, audit=audit)
    res = await runner.run("project.query", {"q": "x"}, actor="a", permission=PermissionLevel.GOVERN, handler=never)
    assert res.status == "TIMEOUT"
    assert "超时" in res.message
    assert "error" in {e["action"] for e in events}


@pytest.mark.asyncio
async def test_error_result_audited_error():
    events = []

    async def boom(args):
        raise RuntimeError("boom")

    async def audit(ev):
        events.append(ev)

    runner = IdempotentToolRunner(timeout_seconds=5, audit=audit)
    res = await runner.run("project.query", {"q": "x"}, actor="a", permission=PermissionLevel.GOVERN, handler=boom)
    assert res.status == "ERROR"
    assert {e["action"] for e in events} == {"start", "error"}


@pytest.mark.asyncio
async def test_cancel_stops_running_run_and_audits():
    events = []
    started = asyncio.Event()

    async def blocking(args):
        started.set()
        await asyncio.sleep(30)

    async def audit(ev):
        events.append(ev)

    runner = IdempotentToolRunner(timeout_seconds=60, audit=audit)

    async def run_and_cancel():
        # 并发跑（不 await 完成），拿到 run_id 后取消
        fut = asyncio.ensure_future(
            runner.run("project.query", {"q": "x"}, actor="a", permission=PermissionLevel.GOVERN, handler=blocking)
        )
        await started.wait()
        # 先确认在跑，再取消
        wait_task = asyncio.ensure_future(
            runner.run("project.query", {"q": "x"}, actor="a", permission=PermissionLevel.GOVERN, handler=blocking)
        )
        dup = await wait_task
        assert dup.status == "RUNNING"
        assert dup.idempotent is True
        assert runner.cancel(dup.run_id) is True
        result = await fut
        return result

    res = await run_and_cancel()
    assert res.status == "CANCELLED"
    assert "cancelled" in {e["action"] for e in events}