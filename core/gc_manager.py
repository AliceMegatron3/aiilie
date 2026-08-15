"""
core/gc_manager.py — 低优先级垃圾回收后台任务管理器（补丁E剩余业务）
====================================================================
职责：
  1. 手动 GC（临时目录清理）立即执行；
  2. SQLite VACUUM 仅在「系统完全空闲」时执行——空闲判定由
     SystemMonitor.is_system_idle() 提供（无 PENDING/RUNNING 任务、
     无量化锁、全局状态非运行态），空闲窗口内轮询等待，超时则跳过
     VACUUM 并在报告中标注原因；
  3. 每个 GC 任务独立 task_id，报告可查询（GET /system/gc/{task_id}/report）。

安全声明：VACUUM 永远不会在系统繁忙时执行；GC 只清理临时目录与
过期尾巴文件，绝不触碰用户项目/文档/卡片数据。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# VACUUM 空闲等待轮询间隔（秒）
_IDLE_POLL_SECONDS = 5.0
# VACUUM 最大空闲等待时长（秒），超时放弃 VACUUM 仅做文件清理
_MAX_IDLE_WAIT_SECONDS = 120.0


class GCTask(BaseModel):
    """垃圾回收任务记录。"""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: str = Field(default="PENDING")  # PENDING/RUNNING/COMPLETED/FAILED
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: Optional[str] = None
    temp_files_removed: int = 0
    temp_bytes_freed: int = 0
    vacuum_done: bool = False
    vacuum_skipped_reason: Optional[str] = None
    error_message: Optional[str] = None


class GCTaskManager:
    """GC 任务提交/执行/报告查询。单 worker 串行执行保证安全。"""

    def __init__(
        self,
        db=None,
        system_monitor=None,
        temp_manager=None,
        tail_manager=None,
        state_manager=None,
    ) -> None:
        self._db = db
        self._monitor = system_monitor
        self._temp_manager = temp_manager
        self._tail_manager = tail_manager
        self._state_manager = state_manager
        self._tasks: dict[str, GCTask] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        # 最近任务缓存上限（防内存无界）
        self._max_keep = 100
        # VACUUM 空闲等待上限（实例属性，测试可调小）
        self._max_idle_wait_seconds = _MAX_IDLE_WAIT_SECONDS

    def submit_gc(self) -> str:
        """提交一个 GC 任务并惰性启动 worker，返回 task_id。"""
        task = GCTask()
        self._tasks[task.task_id] = task
        self._queue.put_nowait(task.task_id)
        self._trim_cache()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._worker_loop())
        logger.info("[GC] 已提交垃圾回收任务: %s", task.task_id)
        return task.task_id

    def get_report(self, task_id: str) -> dict[str, Any] | None:
        """查询任务报告（无则返回 None）。"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return task.model_dump()

    def _trim_cache(self) -> None:
        if len(self._tasks) <= self._max_keep:
            return
        # 按创建时间淘汰最旧已完成任务
        finished = sorted(
            (t for t in self._tasks.values() if t.status in ("COMPLETED", "FAILED")),
            key=lambda t: t.created_at,
        )
        for stale in finished[: len(self._tasks) - self._max_keep]:
            self._tasks.pop(stale.task_id, None)

    async def _worker_loop(self) -> None:
        while True:
            try:
                task_id = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self._run_gc(task_id)
            except Exception as exc:  # 单任务失败不影响后续
                logger.error("[GC] 任务 %s 执行异常: %s", task_id, exc)
            finally:
                self._queue.task_done()

    async def _run_gc(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.status = "RUNNING"
        logger.info("[GC] 开始执行任务 %s：临时文件清理 + 空闲 VACUUM", task_id)

        # ── 1. 临时目录清理（立即执行，不等待空闲） ──
        try:
            removed, freed = await self._cleanup_temp_files()
            task.temp_files_removed = removed
            task.temp_bytes_freed = freed
        except Exception as exc:
            logger.warning("[GC] 临时文件清理异常: %s", exc)

        # ── 2. 空闲等待 + VACUUM ──
        await self._try_vacuum(task)

        task.status = "COMPLETED"
        task.finished_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "[GC] 任务 %s 完成: 清理 %d 文件/%.1fMB, vacuum=%s",
            task_id, task.temp_files_removed,
            task.temp_bytes_freed / (1024 ** 2), task.vacuum_done,
        )

    async def _cleanup_temp_files(self) -> tuple[int, int]:
        """清理过期临时文件与过期尾巴（不含用户数据）。"""
        removed = 0
        freed = 0
        # 过期尾巴清理（TailContextManager TTL 逻辑）
        if self._tail_manager is not None:
            try:
                removed += await asyncio.to_thread(
                    lambda: self._tail_manager.cleanup_expired() or 0
                )
            except Exception:
                pass
        # 临时文件管理器启动式清理（保护活跃任务分段目录）
        if self._temp_manager is not None:
            try:
                if self._db is not None:
                    protected: set[str] = set()
                    for active_row in await self._db.get_active_tasks():
                        for seg_row in await self._db.get_segments_for_task(
                            active_row["task_id"]
                        ):
                            protected.add(seg_row["segment_id"])
                    removed += await asyncio.to_thread(
                        self._temp_manager.startup_cleanup,
                        protected_segment_ids=protected,
                    )
            except Exception:
                pass
        return removed, freed

    async def _try_vacuum(self, task: GCTask) -> None:
        """仅在系统完全空闲时执行 VACUUM（轮询等待，超时跳过）。"""
        if self._db is None:
            task.vacuum_skipped_reason = "db 未注入"
            return

        waited = 0.0
        while not await self.is_system_idle_async():
            if waited >= self._max_idle_wait_seconds:
                task.vacuum_skipped_reason = (
                    f"等待系统空闲超时（>{self._max_idle_wait_seconds:.0f}s），跳过 VACUUM"
                )
                logger.warning("[GC] 任务 %s: %s", task.task_id, task.vacuum_skipped_reason)
                return
            await asyncio.sleep(_IDLE_POLL_SECONDS)
            waited += _IDLE_POLL_SECONDS

        # 空闲窗口内执行 VACUUM
        try:
            logger.info("[GC] 系统空闲，开始执行 SQLite VACUUM...")
            # 注意：VACUUM 不能包在事务内，必须绕开批量写队列直接对连接执行；
            # 空闲判定已保证此刻无活跃写事务。
            await self._db.conn.execute("VACUUM")
            await self._db.conn.commit()
            task.vacuum_done = True
            logger.info("[GC] VACUUM 执行完成")
        except Exception as exc:
            task.vacuum_skipped_reason = f"VACUUM 执行失败: {exc}"
            logger.error("[GC] %s", task.vacuum_skipped_reason)

    def _is_idle(self) -> bool:
        """同步快速空闲判定（量化锁 + 状态管理器同步视图）。"""
        # 1. 量化算力锁
        if self._monitor is not None and self._monitor.is_locked_for_quantization():
            return False
        # 2. 全局状态管理器（非 IDLE 即忙）
        if self._state_manager is not None:
            try:
                from models.system import SystemState

                if self._state_manager._state != SystemState.IDLE:
                    return False
            except Exception:
                pass
        return True

    async def is_system_idle_async(self) -> bool:
        """异步完整空闲判定（含 DB PENDING/RUNNING 任务检查）。"""
        if not self._is_idle():
            return False
        if self._db is not None:
            try:
                active = await self._db.get_active_tasks()
                if active:
                    return False
            except Exception:
                pass
        return True

    async def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
