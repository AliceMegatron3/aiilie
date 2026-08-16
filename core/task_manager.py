"""
core/task_manager.py — 批次5 全局队列 TaskManager
=================================================
实现优先级队列与 SQLite 任务持久化及崩溃恢复逻辑。

⚠️ 与 services.task_manager.TaskManager 的区别（双 TaskManager 语义）：
  - 本模块（批次5 队列版）：submit_task(task: BasePipelineTask) 接收「已构造的
    任务对象」，只做优先级排队 + 状态持久化 + 崩溃恢复；具体业务由 start_workers
    注入的 handler 按 task_type 分派（learning / reflection / quantize）。
  - services.task_manager.TaskManager（批次1 引擎版）：submit_task(raw_command: str)
    接收「指令字符串」，内部走 CommandSplitter 拆分 → SegmentPipeline 分段执行 →
    ResultMerger 合并全链路，面向长任务。
  两者勿混用：混用时本模块请别名 `CoreTaskManager`，services 版别名 `Batch1TaskManager`。
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from core.database import DatabaseManager
from models.task import BasePipelineTask, CommandTask, LearningTask, QuantizeTask, ReflectionTask

logger = logging.getLogger(__name__)

def _parse_task_dict(task_dict: dict[str, Any]) -> BasePipelineTask:
    task_dict = dict(task_dict)
    payload = task_dict.pop("task_payload", None)
    if isinstance(payload, str):
        try:
            import json
            extra = json.loads(payload)
            if isinstance(extra, dict):
                for key, value in extra.items():
                    task_dict.setdefault(key, value)
        except (TypeError, ValueError):
            logger.warning("任务 %s 的扩展 payload 无法解析", task_dict.get("task_id"))
    task_type = task_dict.get("task_type", "command")
    if task_type == "learning":
        return LearningTask(**task_dict)
    elif task_type == "quantize":
        return QuantizeTask(**task_dict)
    elif task_type == "reflection":
        return ReflectionTask(**task_dict)
    else:
        return CommandTask(**task_dict)
class PersistentTaskQueue:
    """持久化任务队列(语义名,优化报告§5.3)。

    职责边界:只做「优先级排队 + 状态持久化 + 崩溃恢复」,不含任何业务;
    业务由 start_workers 注入的 handler 按 task_type 分派。
    与 TaskOrchestrator(services 分段管线)是两种不同机制,不应合并——
    合并会造出上帝类;此处以语义命名消除同名混淆。

    `TaskManager` 保留为向后兼容别名(见模块末尾)。
    """
    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db = db_manager
        # asyncio.PriorityQueue 存储元组: (priority, created_at, task_id, task)
        # priority 越小优先级越高
        self._queue: asyncio.PriorityQueue[tuple[int, str, str, BasePipelineTask]] = asyncio.PriorityQueue()
        self._worker_tasks: list[asyncio.Task] = []
        self._is_running = False
    async def initialize(self) -> None:
        """从数据库中加载未完成的任务以进行恢复。"""
        await self.db.recover_interrupted_tasks()
        pending_tasks = await self.db.get_pending_tasks()
        logger.info("从数据库恢复了 %d 个待处理任务", len(pending_tasks))
        for task_dict in pending_tasks:
            try:
                task = _parse_task_dict(task_dict)
                await self._enqueue_task(task)
            except Exception as e:
                logger.error(f"恢复任务解析失败 {task_dict.get('task_id')}: {e}")
    async def _enqueue_task(self, task: BasePipelineTask) -> None:
        """内部方法，将任务加入内存优先级队列。"""
        priority = task.priority
        created_at = task.created_at.isoformat() if isinstance(task.created_at, datetime) else str(task.created_at)
        await self._queue.put((priority, created_at, task.task_id, task))
    async def submit_task(self, task: BasePipelineTask) -> BasePipelineTask:
        """
        提交一个新任务：
        1. 持久化到 SQLite 数据库
        2. 加入内存优先级队列
        """
        existing = await self.db.get_task(task.task_id)
        if existing is None and task.idempotency_key:
            existing = await self.db.get_task_by_idempotency_key(task.idempotency_key)
        if existing is not None:
            existing_task = _parse_task_dict(existing)
            logger.info("幂等提交命中已有任务: %s", existing_task.task_id)
            return existing_task

        # 写入数据库，双写策略保证不丢失；唯一冲突时重新读取既有任务。
        await self.db.insert_task(task)
        persisted = await self.db.get_task(task.task_id)
        if persisted is None:
            if task.idempotency_key:
                persisted = await self.db.get_task_by_idempotency_key(task.idempotency_key)
            if persisted is None:
                raise RuntimeError(f"任务持久化失败: {task.task_id}")
            existing_task = _parse_task_dict(persisted)
            logger.info("并发提交命中已有幂等任务: %s", existing_task.task_id)
            return existing_task
        # 推送至内存队列
        await self._enqueue_task(task)
        logger.info("任务已提交: %s (优先级: %s)", task.task_id, task.priority)
        return task
    async def update_task_status(
        self, task_id: str, status: str, error_message: str | None = None
    ) -> None:
        """更新任务在数据库中的状态。"""
        await self.db.update_task_status(task_id, status, error_message)
        logger.info("任务 %s 状态变更为: %s", task_id, status)
    async def start_workers(
        self,
        handler: Callable[[BasePipelineTask], Coroutine[Any, Any, None]],
        concurrency: int = 2,
    ) -> None:
        """
        启动后台工作协程，从队列中消费任务。
        
        Args:
            handler: 任务处理回调，接收 BasePipelineTask 实例。
            concurrency: 并发 worker 数量。
        """
        if self._is_running:
            logger.warning("Worker 已经在运行中")
            return
        self._is_running = True
        for i in range(concurrency):
            task = asyncio.create_task(self._worker_loop(i, handler))
            self._worker_tasks.append(task)
        logger.info("启动了 %d 个后台任务 Worker", concurrency)
    async def _worker_loop(
        self,
        worker_id: int,
        handler: Callable[[BasePipelineTask], Coroutine[Any, Any, None]],
    ) -> None:
        """后台消费者的循环逻辑。"""
        while self._is_running:
            try:
                # 阻塞等待任务
                _, _, task_id, task = await self._queue.get()
            except asyncio.CancelledError:
                break
            logger.info("[Worker-%d] 开始处理任务: %s", worker_id, task_id)
            # 原子认领：重复入队、旧队列残留或已取消任务均不得再次执行。
            if not await self.db.claim_task(task_id):
                self._queue.task_done()
                continue
            task.status = "RUNNING"
            try:
                # 执行具体业务逻辑（由外部传入 handler 负责执行 Pipeline）
                await handler(task)
                # 标记为 COMPLETED
                await self.update_task_status(task_id, "COMPLETED")
            except Exception as e:
                logger.exception("[Worker-%d] 任务 %s 执行失败", worker_id, task_id)
                await self.update_task_status(task_id, "FAILED", str(e))
                # 增加重试计数
                await self.db.increment_retry_count(task_id)
            finally:
                self._queue.task_done()

    async def cancel_task(self, task_id: str) -> bool:
        """取消尚未被 worker 认领的任务；已进入终态的任务不可逆。"""
        row = await self.db.get_task(task_id)
        if row is None or row.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
            return False
        return await self.db.update_task_status(task_id, "CANCELLED")
    async def stop(self) -> None:
        """停止所有 worker 并等待当前任务完成。"""
        self._is_running = False
        logger.info("正在停止所有 Worker...")
        for task in self._worker_tasks:
            task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        logger.info("任务管理器已安全停止")

    async def stop_workers(self) -> None:
        """兼容测试及旧调用方的 worker 停止入口。"""
        await self.stop()


# 向后兼容别名:存量 9 处生产引用与测试继续可用 `TaskManager`,
# 新代码请用语义名 PersistentTaskQueue(优化报告§5.3)。
TaskManager = PersistentTaskQueue