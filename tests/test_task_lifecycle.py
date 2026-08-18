"""任务幂等、状态机、取消与重启恢复回归测试。"""
from __future__ import annotations

import asyncio

import pytest

from core.database import DatabaseManager
from core.task_manager import TaskManager as CoreTaskManager
from models.task import BasePipelineTask, TaskStatus
from services.command_splitter import CommandSplitter
from services.result_merger import ResultMerger
from services.segment_pipeline import SegmentPipeline
from services.task_manager import TaskManager as Batch1TaskManager
from services.temp_file_manager import TempFileManager


@pytest.mark.asyncio
async def test_core_idempotency_claim_and_terminal_state(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "core-lifecycle.db")
    await db.initialize()
    manager = CoreTaskManager(db)
    first = BasePipelineTask(task_id="core-first", idempotency_key="request-1")
    duplicate = BasePipelineTask(task_id="core-second", idempotency_key="request-1")
    executed: list[str] = []

    try:
        submitted = await manager.submit_task(first)
        same = await manager.submit_task(duplicate)
        assert submitted.task_id == same.task_id == "core-first"
        assert manager._queue.qsize() == 1

        async def handler(task: BasePipelineTask) -> None:
            executed.append(task.task_id)

        await manager.start_workers(handler, concurrency=2)
        for _ in range(50):
            row = await db.get_task("core-first")
            if executed and row and row["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.01)
        await manager.stop_workers()
        assert executed == ["core-first"]
        assert (await db.get_task("core-first"))["status"] == "COMPLETED"

        assert await db.update_task_status("core-first", "PENDING") is False
        assert (await db.get_task("core-first"))["status"] == "COMPLETED"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_core_recovery_and_cancel_are_safe(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "core-recovery.db")
    await db.initialize()
    try:
        task = BasePipelineTask(task_id="recover-me")
        await db.insert_task(task)
        assert await db.update_task_status(task.task_id, "RUNNING") is True

        recovered = CoreTaskManager(db)
        await recovered.initialize()
        row = await db.get_task(task.task_id)
        assert row["status"] == "PENDING"
        assert recovered._queue.qsize() == 1

        assert await recovered.cancel_task(task.task_id) is True
        assert (await db.get_task(task.task_id))["status"] == "CANCELLED"
        assert await recovered.cancel_task(task.task_id) is False
    finally:
        await db.close()


async def _make_batch1_manager(db: DatabaseManager, tmp_path) -> Batch1TaskManager:
    temp_manager = TempFileManager(temp_root=tmp_path / "temp")

    async def hook(content, tail, source):
        return content, tail

    pipeline = SegmentPipeline(db, temp_manager, execution_hook=hook)
    return Batch1TaskManager(
        db,
        CommandSplitter(),
        pipeline,
        ResultMerger(),
        temp_manager,
    )


@pytest.mark.asyncio
async def test_core_worker_does_not_overwrite_running_cancellation(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "core-cancel-race.db")
    await db.initialize()
    manager = CoreTaskManager(db)
    task = BasePipelineTask(task_id="cancel-during-handler")
    try:
        await manager.submit_task(task)

        async def handler(active: BasePipelineTask) -> None:
            assert await manager.cancel_task(active.task_id) is True

        await manager.start_workers(handler, concurrency=1)
        for _ in range(50):
            row = await db.get_task(task.task_id)
            if row and row["status"] == "CANCELLED":
                break
            await asyncio.sleep(0.01)
        await manager.stop_workers()
        assert (await db.get_task(task.task_id))["status"] == "CANCELLED"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_batch1_idempotency_and_cancel_do_not_duplicate_work(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "batch1-lifecycle.db")
    await db.initialize()
    manager = await _make_batch1_manager(db, tmp_path)
    try:
        first = await manager.submit_task("写一个短场景", idempotency_key="batch1-request")
        same = await manager.submit_task("这次内容不应覆盖原请求", idempotency_key="batch1-request")
        assert first.task_id == same.task_id
        assert manager.queue_size == 1

        assert await manager.cancel_task(first.task_id) is True
        assert (await db.get_task(first.task_id))["status"] == TaskStatus.CANCELLED.value
        assert await manager.process_next() is not None
        assert (await db.get_task(first.task_id))["status"] == TaskStatus.CANCELLED.value
        assert await manager.cancel_task(first.task_id) is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_core_worker_failed_on_handler_error_not_completed(tmp_path):
    """Batch 3：任务是 COMPLETED 的必要条件之一 = handler 真实执行成功。
    缺失/抛错 handler → FAILED，绝不静默吞掉并伪装 COMPLETED。"""
    db = DatabaseManager(db_path=tmp_path / "core-handler-fail.db")
    await db.initialize()
    manager = CoreTaskManager(db)
    good = BasePipelineTask(task_id="ok-task")
    bad = BasePipelineTask(task_id="bad-task")
    try:
        await manager.submit_task(good)
        await manager.submit_task(bad)

        async def handler(task: BasePipelineTask) -> None:
            if task.task_id == "bad-task":
                raise RuntimeError("no real handler / empty input")
            # code_execution/learning 等真实 handler 仅在成功返回后才会被记为 COMPLETED

        await manager.start_workers(handler, concurrency=2)
        for _ in range(100):
            ok_row = await db.get_task("ok-task")
            bad_row = await db.get_task("bad-task")
            if ok_row and ok_row["status"] == "COMPLETED" and bad_row and bad_row["status"] == "FAILED":
                break
            await asyncio.sleep(0.01)
        await manager.stop_workers()
        assert (await db.get_task("ok-task"))["status"] == "COMPLETED"
        assert (await db.get_task("bad-task"))["status"] == "FAILED"
    finally:
        await db.close()