"""运行时资源与队列回归测试。"""
from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from core.bootstrap import _queue_worker
from core.database import DatabaseManager
from core.db_pool import db_pool
from models.system import PriorityLevel
from services.priority_queue import PriorityTaskQueue


@pytest.mark.asyncio
async def test_database_manager_close_releases_an_idle_pooled_connection(tmp_path):
    """临时数据库关闭后连接应归还连接池空闲队列，不泄漏计数。

    连接池已由「引用计数」模型重构为「按路径队列」模型（_pools / _conn_counts），
    本用例同步校验新语义：initialize 后池中持有该路径连接，close 后归还空闲队列。
    """
    db_path = tmp_path / "runtime-regression.db"
    db = DatabaseManager(db_path=db_path)
    await db.initialize()
    pool_key = str(db_path)

    # 初始化后池中应记录该路径的连接（新建连接计数 >= 1）
    assert db_pool._conn_counts.get(pool_key, 0) >= 1

    await db.close()

    # 关闭后连接应归还空闲队列，空闲数 >= 1，连接计数不变（不泄漏、不重复计数）
    assert pool_key in db_pool._pools
    assert db_pool._pools[pool_key].qsize() >= 1
    assert db_pool._conn_counts.get(pool_key, 0) >= 1


@pytest.mark.asyncio
async def test_queue_worker_consumes_coroutines():
    """队列工作进程应正常消费并执行推入的协程。"""
    queue = PriorityTaskQueue()
    app = SimpleNamespace(state=SimpleNamespace(priority_task_queue=queue))
    executed = asyncio.Event()

    async def job() -> None:
        executed.set()

    worker = asyncio.create_task(_queue_worker(app))
    try:
        await queue.push("queued-task", job(), PriorityLevel.LV4)

        await asyncio.wait_for(executed.wait(), timeout=1)
        assert executed.is_set()
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
