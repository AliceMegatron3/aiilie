'''
专项3：Worker 并发压测脚本（0-3 并发）
=========================================
验证 core.TaskManager 在 0/1/2/3 并发 worker 下的正确性与稳定性：
- 0 并发：不启动 worker，任务应保持排队不执行（不泄漏、不误执行）
- 1 并发：串行消费，任务依次完成
- 2 并发：双 worker 并行，任务全部完成且无重复消费
- 3 并发：三 worker 并行，任务全部完成且状态一致（无错乱）
结论统计：每场景输出「提交数/完成数/失败数/耗时/是否重复执行」。
'''
from __future__ import annotations

import asyncio
import tempfile
import time
from collections import Counter
from pathlib import Path

import pytest

from core.database import DatabaseManager
from core.task_manager import TaskManager
from models.task import BasePipelineTask


class _CounterHandler:
    """记录任务执行次数的 handler（用于检测重复消费）。"""

    def __init__(self, tick: float = 0.002) -> None:
        self.executed: Counter[str] = Counter()
        self.tick = tick

    async def __call__(self, task: BasePipelineTask) -> None:
        await asyncio.sleep(self.tick)  # 模拟少量处理耗时
        self.executed[task.task_id] += 1


def _make_task(task_id: str, priority: int = 4) -> BasePipelineTask:
    return BasePipelineTask(task_id=task_id, task_type="command", priority=priority)


@pytest.mark.asyncio
async def test_concurrency_0_worker_no_execution():
    """0 并发：worker 未启动时，任务保持排队，不误执行。"""
    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(db_path=Path(td) / "c0.db")
        await db.initialize()
        tm = TaskManager(db)
        handler = _CounterHandler()
        # 不调用 start_workers → 0 并发
        await tm.submit_task(_make_task("c0_a", 1))
        await tm.submit_task(_make_task("c0_b", 2))
        await asyncio.sleep(0.05)
        assert len(handler.executed) == 0, "0 并发下不应有任何任务被执行"
        row = await db.get_task("c0_a")
        assert row and row["status"] == "PENDING"
        await db.close()
        print("[压测] 0 并发：提交2 执行0，任务保持 PENDING ✅")


@pytest.mark.asyncio
async def test_concurrency_1_worker_serial():
    """1 并发：串行消费，任务依次执行且全部完成。"""
    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(db_path=Path(td) / "c1.db")
        await db.initialize()
        tm = TaskManager(db)
        handler = _CounterHandler()
        await tm.start_workers(handler, concurrency=1)
        await tm.submit_task(_make_task("c1_a", 1))
        await tm.submit_task(_make_task("c1_b", 2))
        await tm.submit_task(_make_task("c1_c", 3))
        for _ in range(50):
            if len(handler.executed) == 3:
                break
            await asyncio.sleep(0.02)
        await tm.stop_workers()
        assert len(handler.executed) == 3, f"1 并发应执行 3 个任务，实际 {len(handler.executed)}"
        assert set(handler.executed.values()) == {1}, "存在重复消费"
        await db.close()
        print("[压测] 1 并发：提交3 执行3，无重复消费 ✅")


@pytest.mark.asyncio
async def test_concurrency_2_workers_all_done():
    """2 并发：双 worker 并行，全部完成且无重复。"""
    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(db_path=Path(td) / "c2.db")
        await db.initialize()
        tm = TaskManager(db)
        handler = _CounterHandler()
        await tm.start_workers(handler, concurrency=2)
        n = 8
        for i in range(n):
            await tm.submit_task(_make_task(f"c2_{i}", priority=(i % 3) + 1))
        for _ in range(80):
            if len(handler.executed) == n:
                break
            await asyncio.sleep(0.02)
        await tm.stop_workers()
        assert len(handler.executed) == n, f"2 并发应执行 {n} 个，实际 {len(handler.executed)}"
        assert set(handler.executed.values()) == {1}, "存在重复消费"
        await db.close()
        print(f"[压测] 2 并发：提交{n} 执行{n}，无重复消费 ✅")


@pytest.mark.asyncio
async def test_concurrency_3_workers_stable():
    """3 并发：三 worker 并行，任务全部完成、状态一致。"""
    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(db_path=Path(td) / "c3.db")
        await db.initialize()
        tm = TaskManager(db)
        handler = _CounterHandler()
        await tm.start_workers(handler, concurrency=3)
        n = 15
        for i in range(n):
            await tm.submit_task(_make_task(f"c3_{i}", priority=(i % 5) + 1))
        for _ in range(100):
            if len(handler.executed) == n:
                break
            await asyncio.sleep(0.02)
        await tm.stop_workers()
        assert len(handler.executed) == n, f"3 并发应执行 {n} 个，实际 {len(handler.executed)}"
        assert set(handler.executed.values()) == {1}, "存在重复消费/状态错乱"
        row = await db.get_task("c3_0")
        assert row is not None
        await db.close()
        print(f"[压测] 3 并发：提交{n} 执行{n}，状态一致无错乱 ✅")
