"""
services/priority_queue.py — 优先级漏斗队列
===================================================
作为任务执行的内存级调度门面，支持高优先级任务（如反思）插队优先处理，
缓解大批量并发写入底层 SQLite 的瞬时锁竞争，起到削峰填谷的作用。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Coroutine

from models.system import PriorityLevel

logger = logging.getLogger(__name__)


@dataclass(order=True)
class QueueItem:
    """队列元素。越小的数值优先级越高。"""
    # 取 PriorityLevel 的负数，保证 Enum 设定的值越大出队越早
    priority_score: int
    task_id: str = field(compare=False)
    coro: Coroutine = field(compare=False)


class PriorityTaskQueue:
    """带高优抢占与中断潜力的调度网关队列"""
    
    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue()
        self.current_task_id: str | None = None
        
    async def push(self, task_id: str, coro: Coroutine, priority: PriorityLevel) -> None:
        """
        向漏斗推送待调度任务。
        priority.value 越高，-priority.value 越小，将在优先队列中排到队首。
        """
        item = QueueItem(priority_score=-priority.value, task_id=task_id, coro=coro)
        await self._queue.put(item)
        logger.info("[PriorityTaskQueue] 任务入列: %s, 优先级等级: %s", task_id, priority.name)
        
    async def pop(self) -> tuple[str, Coroutine]:
        """从漏斗头部弹出当前最高优先级的任务"""
        item = await self._queue.get()
        self.current_task_id = item.task_id
        logger.info("[PriorityTaskQueue] 任务出列准备执行: %s", item.task_id)
        return item.task_id, item.coro

    def task_done(self) -> None:
        """标记任务消费完毕"""
        self.current_task_id = None
        self._queue.task_done()
        
    def qsize(self) -> int:
        """当前排队任务数"""
        return self._queue.qsize()
