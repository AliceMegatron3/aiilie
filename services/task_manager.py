"""
services/task_manager.py — 批次1 分段执行引擎 TaskManager
=========================================================
负责任务的提交、排队、状态更新。
使用 asyncio.PriorityQueue + SQLite 双写策略。

⚠️ 与 core.task_manager.TaskManager 的区别（双 TaskManager 语义）：
  - 本模块（批次1 引擎版）：submit_task(raw_command: str, ...) 接收「指令字符串」，
    内部走 CommandSplitter 拆分 → SegmentPipeline 分段执行 → ResultMerger 合并，
    面向「长任务/创作/量化/深度思考」的分段执行全链路。
  - core.task_manager.TaskManager（批次5 队列版）：submit_task(task: BasePipelineTask)
    接收「已构造的任务对象」，只做优先级排队 + 状态持久化 + 崩溃恢复，
    具体业务由外部 handler 分派（learning/reflection/quantize）。
  两者勿混用：本模块 import 时请统一别名 `Batch1TaskManager`。

P0/P2 修复（本批次）：
  1. 分段结果读取统一走"output_path 磁盘优先"语义（配合 SegmentPipeline 阈值外置）；
  2. 启动恢复时同时恢复分段记录与尾巴现场（TailContextManager 快照），
     提升异常中断恢复粒度；
  3. 新增 get_task_detail / get_task_segments 封装方法，
     供 api/task_api.py 调用，杜绝上层手写 RAW SQL；
  4. cancel_task 支持对 DB 中存在但内存未加载的任务直接取消。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.database import DatabaseManager
from models.task import (
    CommandTask,
    ModelSource,
    Segment,
    SegmentStatus,
    TaskAuditReport,
    TaskStatus,
    TaskStatusResponse,
    normalize_segment_strategy,
)
from services.command_splitter import CommandSplitter
from services.result_merger import ResultMerger
from services.segment_pipeline import SegmentPipeline
from services.tail_context_manager import TailContextManager
from services.temp_file_manager import TempFileManager
from core.config_manager import config_manager

logger = logging.getLogger(__name__)

# 专项4修复：全局硬超时默认值（秒）
DEFAULT_MAX_QUEUE_SECONDS = 3600   # 排队超时
DEFAULT_MAX_RUN_SECONDS = 1800     # 执行超时


def _parse_datetime(value: Any) -> datetime | None:
    """宽松解析 ISO 时间字符串（DB 恢复用）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


class TaskManager:
    """
    任务管理器。

    职责：
    1. 任务提交与分段拆解
    2. 优先级队列调度
    3. 任务状态管理（暂停/恢复/取消）
    4. 审计报告生成
    """

    def __init__(
        self,
        db: DatabaseManager,
        splitter: CommandSplitter,
        pipeline: SegmentPipeline,
        merger: ResultMerger,
        temp_manager: TempFileManager,
        max_queue_size: int = 1000,
        tail_manager: TailContextManager | None = None,
    ) -> None:
        self._db = db
        self._splitter = splitter
        self._pipeline = pipeline
        self._merger = merger
        self._temp_manager = temp_manager
        self._tail_manager = tail_manager
        # 优先级队列：(priority, task_id) — 数值越小优先级越高
        self._queue: asyncio.PriorityQueue[tuple[int, str]] = (
            asyncio.PriorityQueue(maxsize=max_queue_size)
        )
        # 内存中的任务索引
        self._tasks: dict[str, CommandTask] = {}
        # 暂停集合
        self._paused_tasks: set[str] = set()
        # 取消集合
        self._cancelled_tasks: set[str] = set()
        # 专项4修复：全局硬超时（秒），从 config read，支持运行时覆盖
        self._max_queue_seconds = int(
            config_manager.get("task.max_queue_seconds", DEFAULT_MAX_QUEUE_SECONDS)
        )
        self._max_run_seconds = int(
            config_manager.get("task.max_run_seconds", DEFAULT_MAX_RUN_SECONDS)
        )

    @property
    def pipeline(self) -> SegmentPipeline:
        """暴露分段流水线（阶段1：供 bootstrap 在 dispatcher 装配后注入真实执行钩子）。"""
        return self._pipeline

    async def initialize(self) -> None:
        """启动时从数据库恢复未完成的任务（含分段记录与中断现场）。"""
        # P2：恢复非终态任务（PENDING + RUNNING），RUNNING 视为异常中断需重跑。
        await self._db.recover_interrupted_tasks()
        pending = await self._db.get_active_tasks()
        for row in pending:
            task = CommandTask(
                task_id=row["task_id"],
                raw_command=row["raw_command"],
                # 兼容修复：历史数据可能由旧调度链路写入 PriorityLevel 值
                # （40/60 等），超出 CommandTask 的 1-7 校验范围，恢复时钳制
                priority=self._clamp_priority(row.get("priority")),
                status=TaskStatus(row["status"]),
                segment_strategy=row["segment_strategy"],
                model_source=ModelSource(row["model_source"]),
                idempotency_key=row.get("idempotency_key"),
            )
            # P2：恢复分段记录，使异常中断的任务可从中断点继续
            seg_rows = await self._db.get_segments_for_task(task.task_id)
            task.segments = [self._segment_from_row(r) for r in seg_rows]
            # RUNNING 状态回退为 PENDING 重新入队（中断恢复）
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.PENDING
                await self._db.update_task_status(task.task_id, "PENDING")
            self._tasks[task.task_id] = task
            await self._queue.put((task.priority, task.task_id))
        logger.info(
            "任务管理器初始化完成，恢复了 %d 个待处理任务", len(pending)
        )

    @staticmethod
    def _clamp_priority(value: Any) -> int:
        """
        兼容修复：把历史/外部写入的越界优先级钳制到 CommandTask 的 1-7 范围。

        旧调度链路可能把批次5 PriorityLevel 枚举值（LV7=70/LV6=60/LV4=40/LV1=10）
        直接落库，恢复时必须映射回批次1契约，否则 Pydantic 校验直接崩。
        映射关系与 global_router._PRIORITY_LEVEL_TO_BATCH1 保持一致，
        保证历史任务的调度语义（数字越小优先级越高）不劣化。
        """
        _LEGACY_PRIORITY_MAP = {70: 1, 60: 2, 40: 4, 10: 7}
        try:
            priority = int(value)
        except (TypeError, ValueError):
            return 4
        if priority in _LEGACY_PRIORITY_MAP:
            return _LEGACY_PRIORITY_MAP[priority]
        if priority <= 0:
            return 4
        return max(1, min(7, priority))

    @staticmethod
    def _segment_from_row(row: dict[str, Any]) -> Segment:
        """从数据库行恢复 Segment 模型（宽松容错）。"""
        try:
            # SQLite checkpoint 是恢复首选；旧库没有 checkpoint 列时安全回退。
            tail_raw = row.get("checkpoint_tail") or row.get("tail_context") or "{}"
            tail = json.loads(tail_raw) if isinstance(tail_raw, str) else dict(tail_raw)
            if not isinstance(tail, dict):
                tail = {}
        except (json.JSONDecodeError, TypeError, ValueError):
            tail = {}
        return Segment(
            segment_id=row["segment_id"],
            parent_task_id=row["parent_task_id"],
            content_payload=row["content_payload"],
            sequence_order=row["sequence_order"],
            status=SegmentStatus(row["status"]),
            tail_context=tail,
            result_content=row.get("result_content") or row.get("checkpoint_content"),
            output_path=row.get("output_path"),
            started_at=_parse_datetime(row.get("started_at")),
            completed_at=_parse_datetime(row.get("completed_at")),
            error_message=row.get("error_message"),
        )

    async def submit_task(
        self,
        raw_command: str,
        priority: int = 4,
        segment_strategy: str = "auto",
        model_source: str = "local",
        idempotency_key: str | None = None,
        project_id: str | None = None,
    ) -> CommandTask:
        """
        提交新任务。

        流程：
        1. 创建 CommandTask 对象
        2. 使用 CommandSplitter 拆解为 Segment 列表
        3. 双写到内存队列和 SQLite

        补丁：segment_strategy 防御性兜底——
        非法字符串自动回退 auto，防止脏参数导致任务崩溃；
        显式传入 force_split / no_split / auto 则原样生效。
        """
        # ── 补丁：策略参数归一化 + 兜底回退 ──
        raw_strategy = str(segment_strategy or "auto").strip().lower()
        if raw_strategy in self._splitter._strategies:
            # 运行时注册的自定义策略优先保留
            segment_strategy = raw_strategy
        else:
            segment_strategy = normalize_segment_strategy(raw_strategy)
        if segment_strategy == "auto" and raw_strategy not in (
            "auto", "by_length", "by_chapter",
        ):
            logger.warning(
                "[SPLIT_STRATEGY:auto] 提交任务收到非法分段策略 %r，"
                "已兜底回退 auto",
                raw_strategy,
            )

        if idempotency_key:
            existing = await self._db.get_task_by_idempotency_key(idempotency_key)
            if existing is not None:
                cached = self._tasks.get(existing["task_id"])
                if cached is not None:
                    return cached
                existing_task = CommandTask(
                    task_id=existing["task_id"],
                    raw_command=existing["raw_command"],
                    priority=self._clamp_priority(existing.get("priority")),
                    status=TaskStatus(existing["status"]),
                    segment_strategy=existing.get("segment_strategy", "auto"),
                    model_source=ModelSource(existing.get("model_source", "local")),
                    idempotency_key=existing.get("idempotency_key"),
                )
                existing_task.segments = [
                    self._segment_from_row(row)
                    for row in await self._db.get_segments_for_task(existing_task.task_id)
                ]
                self._tasks[existing_task.task_id] = existing_task
                return existing_task

        task = CommandTask(
            raw_command=raw_command,
            priority=priority,
            segment_strategy=segment_strategy,
            model_source=ModelSource(model_source),
            idempotency_key=idempotency_key,
        )

        # 分段拆解
        segments = self._splitter.split(task)
        task.segments = segments

        # 阶段1：项目绑定注入首段尾巴，随尾巴接力传递到执行钩子，
        # 供 dispatcher 加载项目绑定书库的卡片上下文（世界观上下文进入主链路）
        if project_id and segments:
            first_tail = dict(getattr(segments[0], "tail_context", None) or {})
            first_tail["project_id"] = project_id
            segments[0].tail_context = first_tail

        # 写入数据库
        await self._db.insert_task(task)
        persisted = await self._db.get_task(task.task_id)
        if persisted is None:
            if idempotency_key:
                persisted = await self._db.get_task_by_idempotency_key(idempotency_key)
            if persisted is None:
                raise RuntimeError(f"任务持久化失败: {task.task_id}")
            existing_task = self._tasks.get(persisted["task_id"])
            if existing_task is not None:
                return existing_task
            existing_task = CommandTask(
                task_id=persisted["task_id"],
                raw_command=persisted["raw_command"],
                priority=self._clamp_priority(persisted.get("priority")),
                status=TaskStatus(persisted["status"]),
                segment_strategy=persisted.get("segment_strategy", "auto"),
                model_source=ModelSource(persisted.get("model_source", "local")),
                idempotency_key=persisted.get("idempotency_key"),
            )
            existing_task.segments = [
                self._segment_from_row(row)
                for row in await self._db.get_segments_for_task(existing_task.task_id)
            ]
            self._tasks[existing_task.task_id] = existing_task
            return existing_task
        if persisted.get("task_id") != task.task_id:
            existing_task = self._tasks.get(persisted["task_id"])
            if existing_task is not None:
                return existing_task
            return await self.submit_task(
                raw_command=persisted["raw_command"],
                priority=self._clamp_priority(persisted.get("priority")),
                segment_strategy=persisted.get("segment_strategy", "auto"),
                model_source=persisted.get("model_source", "local"),
                idempotency_key=persisted.get("idempotency_key"),
            )
        for seg in segments:
            await self._db.insert_segment(seg.model_dump(mode="json"))

        # 写入内存
        self._tasks[task.task_id] = task
        await self._queue.put((task.priority, task.task_id))

        logger.info(
            "任务已提交: %s (优先级=%d, 分段数=%d, 模型来源=%s)",
            task.task_id, task.priority, len(segments), task.model_source.value,
        )
        return task

    async def process_next(self) -> CommandTask | None:
        """
        从队列获取并处理下一个任务。

        流程：
        1. 按优先级获取任务
        2. 检查是否被暂停或取消
        3. 执行所有分段
        4. 合并结果
        5. 清理临时文件
        """
        try:
            priority, task_id = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

        task = self._tasks.get(task_id)
        if task is None:
            logger.warning("任务 %s 在队列中但未找到记录", task_id)
            self._queue.task_done()
            return None

        persisted = await self._db.get_task(task_id)
        if persisted is None or persisted.get("status") in {
            "COMPLETED", "FAILED", "CANCELLED",
        }:
            self._queue.task_done()
            return task

        # ── 专项4修复：全局硬超时（排队阶段） ──
        created = task.created_at
        if created is not None:
            waited = (datetime.now(timezone.utc) - created).total_seconds()
            if waited > self._max_queue_seconds:
                task.status = TaskStatus.FAILED
                await self._db.update_task_status(
                    task_id, "FAILED",
                    error_message=f"任务排队超时（{waited:.0f}s > {self._max_queue_seconds}s），已被强制终止",
                )
                logger.warning(
                    "任务 %s 排队超时 %.0fs，强制终止", task_id, waited,
                )
                self._queue.task_done()
                return task

        # 检查取消
        if task_id in self._cancelled_tasks:
            task.status = TaskStatus.CANCELLED
            await self._db.update_task_status(task_id, "CANCELLED")
            self._cancelled_tasks.discard(task_id)
            logger.info("任务 %s 已被取消", task_id)
            self._queue.task_done()
            return task

        # 检查暂停
        if task_id in self._paused_tasks:
            # 重新入队
            await self._queue.put((priority, task_id))
            self._queue.task_done()
            logger.info("任务 %s 已暂停，重新入队", task_id)
            return None

        # 原子认领，防止重复队列项导致同一任务被并发执行。
        if not await self._db.claim_task(task_id):
            self._queue.task_done()
            return task

        # 标记为运行中
        task.status = TaskStatus.RUNNING
        task.updated_at = datetime.now(timezone.utc)
        await self._db.update_task_status(task_id, "RUNNING")

        # 登记运行中任务，保护其临时目录免遭 LRU 淘汰
        self._temp_manager.register_active_task(
            task_id, [s.segment_id for s in task.segments]
        )

        try:
            from core.circuit_breaker import model_circuit_breaker
            model_circuit_breaker.check()

            # 执行所有分段（按顺序，传递 tail_context）
            # 架构整改 1.1：任务绑定的 branch_id 贯穿全程（尾巴分支隔离）
            # 专项4修复：全局硬超时（执行阶段）——超时强制终止并落 FAILED 终态
            async def _run_all() -> list:
                return await self._pipeline.execute_all(
                    task.segments,
                    task.model_source.value,
                    branch_id=getattr(task, "branch_id", None),
                )
            try:
                executed_segments = await asyncio.wait_for(
                    _run_all(), timeout=self._max_run_seconds
                )
            except asyncio.TimeoutError:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now(timezone.utc)
                await self._db.update_task_status(
                    task_id, "FAILED",
                    error_message=f"任务执行超时（>{self._max_run_seconds}s），已被强制终止",
                )
                logger.error(
                    "任务 %s 执行超时（%ss），强制终止", task_id, self._max_run_seconds,
                )
                return task
            
            model_circuit_breaker.record_success()
            
            task.segments = executed_segments

            # 检查是否所有分段都成功
            all_completed = all(
                s.status == SegmentStatus.COMPLETED for s in executed_segments
            )

            if all_completed:
                # 合并结果
                merged = await self._merger.merge(task)
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(timezone.utc)
                await self._db.update_task_status(task_id, "COMPLETED")
                logger.info("任务 %s 执行完成，结果已合并", task_id)
            else:
                failed_count = sum(
                    1 for s in executed_segments
                    if s.status == SegmentStatus.FAILED
                )
                task.status = TaskStatus.FAILED
                task.error_message = f"{failed_count} 个分段执行失败"
                await self._db.update_task_status(
                    task_id, "FAILED", error_message=task.error_message
                )
                logger.error("任务 %s 部分分段失败 (%d/%d)",
                             task_id, failed_count, len(executed_segments))

        except Exception as e:
            from core.circuit_breaker import model_circuit_breaker, CircuitBreakerOpenException
            if not isinstance(e, CircuitBreakerOpenException):
                model_circuit_breaker.record_failure()
                
            task.status = TaskStatus.FAILED
            task.error_message = f"执行异常: {e}"
            await self._db.update_task_status(
                task_id, "FAILED", error_message=task.error_message
            )
            await self._db.increment_retry_count(task_id)
            logger.error("任务 %s 发生未捕获异常: %s", task_id, e, exc_info=True)
            return task

        finally:
            # 清理临时文件（cleanup_task 内部会解除运行中保护）
            seg_ids = [s.segment_id for s in task.segments]
            self._temp_manager.cleanup_task(task_id, seg_ids)
            task.updated_at = datetime.now(timezone.utc)
            self._queue.task_done()

        return task

    async def pause_task(self, task_id: str) -> bool:
        """暂停指定任务。"""
        if task_id not in self._tasks:
            return False
        self._paused_tasks.add(task_id)
        task = self._tasks[task_id]
        task.status = TaskStatus.PAUSED
        await self._db.update_task_status(task_id, "PAUSED")
        logger.info("任务 %s 已暂停", task_id)
        return True

    async def resume_task(self, task_id: str) -> bool:
        """恢复暂停的任务。"""
        if task_id not in self._paused_tasks:
            return False
        self._paused_tasks.discard(task_id)
        task = self._tasks[task_id]
        task.status = TaskStatus.PENDING
        await self._db.update_task_status(task_id, "PENDING")
        logger.info("任务 %s 已恢复", task_id)
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """
        取消指定任务。

        支持三类场景：
        1. 任务在内存队列中：标记取消，出队时终止；
        2. 任务仅存在于数据库（如进程重启后未恢复）：直接落库取消；
        3. 已取消但未出队（重复取消）：返回 True 幂等，保证前端可重试。
        """
        if task_id in self._tasks:
            task = self._tasks[task_id]
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                logger.info("任务 %s 已处于终态 %s，无法取消", task_id, task.status.value)
                return False
            # 已标记取消但尚未出队时幂等返回 True，避免前端误报"无法取消"
            if task_id in self._cancelled_tasks:
                logger.info("任务 %s 已处于取消待出队状态（幂等返回）", task_id)
                return True
            self._cancelled_tasks.add(task_id)
            # 【专项4修复】取消必须立即落库终态：
            # 原实现仅标记内存集合，导致查询仍返回 PENDING（0-2 复盘实锤）。
            # 现在同步将 DB 状态置为 CANCELLED，保证外部查询立即看到终态；
            # 内存任务后续出队时 process_next 仍会依据 _cancelled_tasks 跳过执行。
            try:
                updated = await self._db.update_task_status(task_id, "CANCELLED")
                if not updated:
                    self._cancelled_tasks.discard(task_id)
                    return False
                task.status = TaskStatus.CANCELLED
            except Exception as exc:
                logger.error("任务 %s 取消落库失败: %s", task_id, exc)
            logger.info("任务 %s 已标记为取消并落库终态", task_id)
            return True
        # 内存无记录，尝试直接从数据库取消
        row = await self._db.get_task(task_id)
        if row is None:
            return False
        if row.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
            logger.info("任务 %s 已处于终态 %s，无法取消",
                        task_id, row.get("status"))
            return False
        updated = await self._db.update_task_status(task_id, "CANCELLED")
        logger.info("任务 %s 已从数据库直接取消", task_id)
        return updated

    async def get_task_status(self, task_id: str) -> TaskStatusResponse | None:
        """查询任务状态。"""
        task = self._tasks.get(task_id)
        if task is None:
            # 尝试从数据库查询
            row = await self._db.get_task(task_id)
            if row is None:
                return None
            segments = await self._db.get_segments_for_task(task_id)
            completed = sum(1 for s in segments if s["status"] == "COMPLETED")
            return TaskStatusResponse(
                task_id=row["task_id"],
                status=TaskStatus(row["status"]),
                priority=row["priority"],
                segment_count=len(segments),
                completed_segments=completed,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row.get("completed_at"),
                error_message=row.get("error_message"),
            )

        completed = sum(
            1 for s in task.segments if s.status == SegmentStatus.COMPLETED
        )
        return TaskStatusResponse(
            task_id=task.task_id,
            status=task.status,
            priority=task.priority,
            segment_count=len(task.segments),
            completed_segments=completed,
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
            error_message=task.error_message,
        )

    async def get_task_detail(self, task_id: str) -> dict[str, Any] | None:
        """
        查询任务详情（P2-5 供 api/task_api.py 调用的封装方法）。

        返回字段为旧 /tasks/{task_id}/status 接口的超集，
        保证上层前端向后兼容：
            task_id / status / retry_count / priority / progress /
            created_at / updated_at / completed_at / error_message /
            segment_strategy / model_source
        本方法内部只走 DatabaseManager 封装，禁止调用方手写 SQL。
        """
        row = await self._db.get_task(task_id)
        if row is None:
            return None

        # 分段信息优先取内存（执行中的任务），否则落库查询
        task = self._tasks.get(task_id)
        if task is not None and task.segments:
            total = len(task.segments)
            completed = sum(
                1 for s in task.segments if s.status == SegmentStatus.COMPLETED
            )
        else:
            seg_rows = await self._db.get_segments_for_task(task_id)
            total = len(seg_rows)
            completed = sum(
                1 for s in seg_rows if s.get("status") == "COMPLETED"
            )

        return {
            "task_id": row["task_id"],
            "status": row.get("status"),
            "retry_count": row.get("retry_count", 0),
            "priority": row.get("priority"),
            "progress": {
                "total": total,
                "completed": completed,
                "percent": (
                    round(completed / total * 100, 2) if total > 0 else 0
                ),
            },
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "completed_at": row.get("completed_at"),
            "error_message": row.get("error_message"),
            "segment_strategy": row.get("segment_strategy"),
            "model_source": row.get("model_source"),
        }

    async def get_task_segments(
        self, task_id: str
    ) -> list[dict[str, Any]] | None:
        """
        查询任务的全部分段列表（P2-5 供 api/task_api.py 调用的封装方法）。

        - 任务不存在时返回 None；
        - 每段附加 result_available 标志（output_path 文件存在或
          result_content 存在即为可用），大内容不整体搬运到响应中。
        """
        row = await self._db.get_task(task_id)
        if row is None:
            return None

        task = self._tasks.get(task_id)
        if task is not None and task.segments:
            items: list[dict[str, Any]] = []
            for seg in task.segments:
                items.append(self._segment_to_api_dict(seg))
            return items

        seg_rows = await self._db.get_segments_for_task(task_id)
        return [self._row_to_api_dict(r) for r in seg_rows]

    @staticmethod
    def _segment_to_api_dict(seg: Segment) -> dict[str, Any]:
        """内存 Segment 模型 → API 字典（不搬运大内容）。"""
        output_ok = bool(seg.output_path) and Path(seg.output_path).exists()
        return {
            "segment_id": seg.segment_id,
            "parent_task_id": seg.parent_task_id,
            "sequence_order": seg.sequence_order,
            "status": seg.status.value,
            "content_payload_length": len(seg.content_payload),
            "result_content": seg.result_content,  # 短内容才在内存中
            "output_path": seg.output_path,
            "result_available": output_ok or bool(seg.result_content),
            "started_at": (
                seg.started_at.isoformat() if seg.started_at else None
            ),
            "completed_at": (
                seg.completed_at.isoformat() if seg.completed_at else None
            ),
            "error_message": seg.error_message,
        }

    @staticmethod
    def _row_to_api_dict(row: dict[str, Any]) -> dict[str, Any]:
        """DB 分段行 → API 字典（不搬运大内容）。"""
        output_path = row.get("output_path")
        output_ok = bool(output_path) and Path(output_path).exists()
        return {
            "segment_id": row["segment_id"],
            "parent_task_id": row["parent_task_id"],
            "sequence_order": row["sequence_order"],
            "status": row.get("status"),
            "content_payload_length": len(row.get("content_payload") or ""),
            "result_content": row.get("result_content"),  # 短内容才在 DB 中
            "output_path": output_path,
            "result_available": output_ok or bool(row.get("result_content")),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "error_message": row.get("error_message"),
        }

    async def generate_task_audit_report(self, task_id: str) -> TaskAuditReport | None:
        """
        生成任务审计报告。

        返回执行耗时、分段数、成功率等统计数据。
        """
        task = self._tasks.get(task_id)
        if task is None:
            row = await self._db.get_task(task_id)
            if row is None:
                return None
            segments_data = await self._db.get_segments_for_task(task_id)
            total = len(segments_data)
            completed = sum(1 for s in segments_data if s["status"] == "COMPLETED")
            failed = sum(1 for s in segments_data if s["status"] == "FAILED")
            success_rate = (completed / total * 100) if total > 0 else 0.0

            # 计算总耗时
            duration = None
            if row.get("created_at") and row.get("completed_at"):
                try:
                    start = datetime.fromisoformat(row["created_at"])
                    end = datetime.fromisoformat(row["completed_at"])
                    duration = (end - start).total_seconds()
                except (ValueError, TypeError):
                    pass

            avg_duration = (duration / total) if duration and total > 0 else None

            return TaskAuditReport(
                task_id=task_id,
                total_segments=total,
                completed_segments=completed,
                failed_segments=failed,
                success_rate=round(success_rate, 2),
                total_duration_seconds=duration,
                average_segment_duration_seconds=(
                    round(avg_duration, 2) if avg_duration else None
                ),
                status=TaskStatus(row["status"]),
            )

        # 从内存中的任务生成报告
        total = len(task.segments)
        completed = sum(
            1 for s in task.segments if s.status == SegmentStatus.COMPLETED
        )
        failed = sum(
            1 for s in task.segments if s.status == SegmentStatus.FAILED
        )
        success_rate = (completed / total * 100) if total > 0 else 0.0

        duration = None
        if task.created_at and task.completed_at:
            duration = (task.completed_at - task.created_at).total_seconds()

        avg_duration = (duration / total) if duration and total > 0 else None

        return TaskAuditReport(
            task_id=task_id,
            total_segments=total,
            completed_segments=completed,
            failed_segments=failed,
            success_rate=round(success_rate, 2),
            total_duration_seconds=duration,
            average_segment_duration_seconds=(
                round(avg_duration, 2) if avg_duration else None
            ),
            status=task.status,
        )

    @property
    def queue_size(self) -> int:
        """当前队列中的任务数。"""
        return self._queue.qsize()

    @property
    def total_tasks(self) -> int:
        """内存中跟踪的任务总数。"""
        return len(self._tasks)


# 语义名(优化报告§5.3):本类是「分段执行编排器」——指令→拆分→分段→合并。
# 与 core.task_manager.PersistentTaskQueue(纯队列持久化)是两种机制,
# 职责不同不予合并;以语义命名消除同名混淆。存量引用继续用 TaskManager。
TaskOrchestrator = TaskManager
