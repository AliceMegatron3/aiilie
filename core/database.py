"""
core/database.py — 异步 SQLite 数据库管理器
============================================
使用 aiosqlite 提供异步数据库操作。
双写策略：内存队列（调度性能）+ SQLite（崩溃恢复）。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.task import BasePipelineTask
import aiosqlite
from core.path_resolver import get_db_path
logger = logging.getLogger(__name__)

_WRITE_QUEUE_SIZE = 1000
_WRITE_BATCH_SIZE = 100
_LOCK_RETRY_COUNT = 6
_LOCK_RETRY_DELAY = 0.15


def _is_locked_error(exc: BaseException) -> bool:
    """仅对 SQLite 锁竞争重试，避免掩盖 SQL/数据错误。"""
    return isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).lower() for marker in ("database is locked", "database is busy")
    )
# SQL 建表语句
_CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id         TEXT PRIMARY KEY,
    task_type       TEXT NOT NULL DEFAULT 'command',
    raw_command     TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 4,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    segment_strategy TEXT NOT NULL DEFAULT 'auto',
    model_source    TEXT NOT NULL DEFAULT 'local',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT,
    error_message   TEXT,
    task_payload    TEXT,
    idempotency_key TEXT
)
"""
_CREATE_TASK_IDEMPOTENCY_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency_key
ON tasks(idempotency_key)
WHERE idempotency_key IS NOT NULL
"""
_CREATE_SEGMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS segments (
    segment_id      TEXT PRIMARY KEY,
    parent_task_id  TEXT NOT NULL,
    content_payload TEXT NOT NULL,
    sequence_order  INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    tail_context    TEXT NOT NULL DEFAULT '{}',
    result_content  TEXT,
    output_path     TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    error_message   TEXT,
    FOREIGN KEY (parent_task_id) REFERENCES tasks(task_id)
)
"""
_CREATE_SEGMENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_segments_task
ON segments(parent_task_id, sequence_order)
"""

_CREATE_CHAT_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS chat_history (
    task_id         TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    project_id      TEXT,
    user_query      TEXT NOT NULL,
    ai_result       TEXT NOT NULL,
    timestamp       TEXT NOT NULL
)
"""

_CREATE_MODEL_CREDENTIALS_TABLE = """
CREATE TABLE IF NOT EXISTS model_credentials (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    api_endpoint  TEXT NOT NULL,
    api_key       TEXT,
    model_tags    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""

_CREATE_EMOTION_FRAMES_TABLE = """
CREATE TABLE IF NOT EXISTS emotion_frames (
    frame_id            TEXT PRIMARY KEY,
    frame_type          TEXT NOT NULL,
    source_project_id   TEXT,
    source_book_id      TEXT,
    wave_level          INTEGER NOT NULL,
    mode                TEXT NOT NULL,
    file_path           TEXT NOT NULL,
    create_time         TEXT NOT NULL
)
"""

class DatabaseManager:
    """异步 SQLite 数据库管理器，管理连接池和表初始化。"""
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or get_db_path()
        self._connection: aiosqlite.Connection | None = None
        self._from_pool = False
        # 有界队列提供后台批量写库的背压，避免协程无限堆积写请求。
        self._write_queue: asyncio.Queue = asyncio.Queue(maxsize=_WRITE_QUEUE_SIZE)
        self._writer_task: asyncio.Task | None = None
        self._process_lock_file = None

    def _acquire_packaged_process_lock(self) -> None:
        """打包后禁止两个后端进程同时打开同一 SQLite 文件。"""
        if not (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")):
            return
        lock_path = Path(f"{self._db_path}.process.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - frozen发布环境为Windows
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            handle.close()
            raise RuntimeError(
                f"检测到已有打包后端正在使用数据库 {self._db_path}，禁止多进程并发打开"
            ) from exc
        self._process_lock_file = handle

    def _release_packaged_process_lock(self) -> None:
        handle = self._process_lock_file
        self._process_lock_file = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            logger.warning("释放 SQLite 进程锁失败: %s", self._db_path, exc_info=True)
        finally:
            handle.close()
    async def initialize(self) -> None:
        """初始化数据库连接和表结构。

        性能优化：优先复用 db_pool 的共享连接（避免重复 open/close）；
        池不可用时（如独立测试场景）退化为自建连接。
        """
        logger.info("正在初始化数据库: %s", self._db_path)
        self._acquire_packaged_process_lock()
        try:
            from core.db_pool import db_pool

            self._connection = await db_pool.acquire(self._db_path)
            self._from_pool = True
        except Exception as pool_exc:  # pragma: no cover
            logger.warning("连接池不可用，退化为自建连接: %s", pool_exc)
            self._connection = await aiosqlite.connect(str(self._db_path))

        try:
            from core.db_pool import db_pool

            async with db_pool.write_lock(self._db_path):
                await self._initialize_schema_with_retry()
        except Exception:
            await self.close()
            raise
        
        self._writer_task = asyncio.create_task(self._writer_worker())
        logger.info("数据库初始化完成, 后台写入队列已启动")
    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        """检查表中是否存在指定列，不存在则添加（SQLite ALTER TABLE 兼容）。"""
        try:
            cursor = await self._connection.execute(f"PRAGMA table_info({table})")
            rows = await cursor.fetchall()
            columns = [row[1] for row in rows]
            if column not in columns:
                await self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
                logger.info("已为表 %s 添加列 %s", table, column)
        except Exception as e:
            logger.warning("检查/添加列 %s.%s 失败: %s", table, column, e)

    async def _initialize_schema_with_retry(self) -> None:
        """DDL 与兼容迁移使用同一事务，并对锁竞争做有限退避重试。"""
        for attempt in range(_LOCK_RETRY_COUNT + 1):
            try:
                await self._connection.execute("PRAGMA journal_mode=WAL")
                await self._connection.execute("PRAGMA synchronous=NORMAL")
                await self._connection.execute("PRAGMA busy_timeout=10000")
                await self._connection.execute("PRAGMA foreign_keys=ON")
                await self._connection.execute("BEGIN IMMEDIATE")
                for statement in (
                    _CREATE_TASKS_TABLE,
                    _CREATE_SEGMENTS_TABLE,
                    _CREATE_SEGMENTS_INDEX,
                    _CREATE_CHAT_HISTORY_TABLE,
                    _CREATE_MODEL_CREDENTIALS_TABLE,
                    _CREATE_EMOTION_FRAMES_TABLE,
                ):
                    await self._connection.execute(statement)
                await self._ensure_column("tasks", "retry_count", "INTEGER NOT NULL DEFAULT 0")
                await self._ensure_column("tasks", "task_type", "TEXT NOT NULL DEFAULT 'command'")
                await self._ensure_column("tasks", "task_payload", "TEXT")
                await self._ensure_column("tasks", "idempotency_key", "TEXT")
                await self._connection.execute(_CREATE_TASK_IDEMPOTENCY_INDEX)
                await self._ensure_column("segments", "checkpoint_content", "TEXT")
                await self._ensure_column("segments", "checkpoint_tail", "TEXT")
                await self._ensure_column("segments", "checkpoint_summary", "TEXT")
                await self._connection.commit()
                return
            except Exception as exc:
                try:
                    await self._connection.rollback()
                except Exception:
                    pass
                if not _is_locked_error(exc) or attempt >= _LOCK_RETRY_COUNT:
                    raise
                await asyncio.sleep(_LOCK_RETRY_DELAY * (attempt + 1))
    async def close(self) -> None:
        """释放数据库连接（连接池模式下仅归还引用，由池统一回收）。"""
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None
            
        if self._connection:
            if self._from_pool:
                from core.db_pool import db_pool

                # 临时库必须立刻关闭文件句柄（Windows TemporaryDirectory 会
                # 随即删除文件）；连接池保留一个关闭占位符以维持池计数契约。
                close_connection = self._db_path != get_db_path()
                await db_pool.release(
                    self._connection, self._db_path, close_connection=close_connection
                )
            else:
                await self._connection.close()
        self._connection = None
        self._release_packaged_process_lock()
        logger.info("数据库连接已释放")

    async def _writer_worker(self) -> None:
        """后台单写线程，批量处理最多 100 条写入命令，避免锁竞争。"""
        while True:
            try:
                batch = []
                item = await self._write_queue.get()
                batch.append(item)
                
                while len(batch) < _WRITE_BATCH_SIZE and not self._write_queue.empty():
                    batch.append(self._write_queue.get_nowait())

                try:
                    from core.db_pool import db_pool

                    async with db_pool.write_lock(self._db_path):
                        await self._execute_batch_with_retry(batch)
                except Exception as batch_e:
                    logger.error("批量事务提交失败: %s", batch_e, exc_info=True)
                    for _, _, future in batch:
                        if future and not future.done():
                            future.set_exception(batch_e)
                else:
                    for _, _, future in batch:
                        if future and not future.done():
                            future.set_result(None)
                finally:
                    for _ in batch:
                        self._write_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("数据库写入线程发生异常: %s", e)
                await asyncio.sleep(1)

    async def _execute_batch_with_retry(self, batch: list[tuple]) -> None:
        for attempt in range(_LOCK_RETRY_COUNT + 1):
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                for query, params, _ in batch:
                    await self.conn.execute(query, params)
                await self.conn.commit()
                return
            except Exception as exc:
                try:
                    await self.conn.rollback()
                except Exception:
                    pass
                if not _is_locked_error(exc) or attempt >= _LOCK_RETRY_COUNT:
                    raise
                logger.warning(
                    "SQLite 写事务遇到锁，%d/%d 次退避重试: %s",
                    attempt + 1, _LOCK_RETRY_COUNT, exc,
                )
                await asyncio.sleep(_LOCK_RETRY_DELAY * (attempt + 1))

    async def execute_write(self, query: str, params: tuple = ()) -> None:
        """将写操作压入队列串行处理。"""
        future = asyncio.get_running_loop().create_future()
        await self._write_queue.put((query, params, future))
        await future
    @property
    def conn(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("数据库尚未初始化，请先调用 initialize()")
        return self._connection
    # ── 任务 CRUD ─────────────────────────────────────────────────
    async def insert_task(self, task: BasePipelineTask) -> None:
        now = datetime.now(timezone.utc).isoformat()
        task_data = task.model_dump(mode="json")
        payload = {
            key: value
            for key, value in task_data.items()
            if key not in {
                "task_id", "task_type", "raw_command", "priority", "status",
                "segment_strategy", "model_source", "retry_count", "created_at",
                "updated_at", "completed_at", "error_message", "idempotency_key",
                "segments",
            }
        }
        await self.execute_write(
            """INSERT INTO tasks
               (task_id, task_type, raw_command, priority, status, segment_strategy,
                model_source, retry_count, created_at, updated_at, task_payload,
                idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT DO NOTHING""",
            (
                task.task_id,
                task_data.get("task_type", "command"),
                task_data.get("raw_command", ""),
                task.priority,
                task.status.value if hasattr(task.status, "value") else task.status,
                task_data.get("segment_strategy", "auto"),
                task_data.get("model_source", "local"),
                task.retry_count,
                now,
                now,
                json.dumps(payload, ensure_ascii=False),
                task_data.get("idempotency_key"),
            ),
        )

    async def get_task_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM tasks WHERE idempotency_key=?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    async def recover_interrupted_tasks(self) -> int:
        """将进程退出时遗留的 RUNNING 任务恢复为可重新认领的 PENDING。"""
        now = datetime.now(timezone.utc).isoformat()
        await self.execute_write(
            """UPDATE tasks SET status='PENDING', updated_at=?
               WHERE status='RUNNING'""",
            (now,),
        )
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='PENDING' AND updated_at=?",
            (now,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def claim_task(self, task_id: str) -> bool:
        """原子认领一个 PENDING 任务，防止重复入队导致重复执行。"""
        now = datetime.now(timezone.utc).isoformat()
        from core.db_pool import db_pool

        async with db_pool.write_lock(self._db_path):
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """UPDATE tasks SET status='RUNNING', updated_at=?,
                       error_message=NULL WHERE task_id=? AND status='PENDING'""",
                    (now, task_id),
                )
                await self.conn.commit()
                return cursor.rowcount == 1
            except Exception:
                await self.conn.rollback()
                raise

    async def update_task_status(
        self, task_id: str, status: str, error_message: str | None = None
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        completed = now if status in ("COMPLETED", "FAILED") else None
        await self.execute_write(
            """UPDATE tasks SET status=?, updated_at=?, completed_at=?,
               error_message=?
               WHERE task_id=? AND (status NOT IN ('COMPLETED','FAILED','CANCELLED')
               OR status=?)""",
            (status, now, completed, error_message, task_id, status),
        )
        row = await self.get_task(task_id)
        return row is not None and row.get("status") == status
    async def increment_retry_count(self, task_id: str) -> None:
        """任务失败时增加重试计数。"""
        await self.execute_write(
            "UPDATE tasks SET retry_count = retry_count + 1 WHERE task_id=?",
            (task_id,),
        )
    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))
    async def get_pending_tasks(self) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM tasks WHERE status='PENDING' ORDER BY priority ASC"
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        """
        获取非终态任务（PENDING + RUNNING），
        供批次1引擎启动恢复与临时目录保护使用。
        """
        cursor = await self.conn.execute(
            "SELECT * FROM tasks WHERE status IN ('PENDING','RUNNING')"
            " ORDER BY priority ASC"
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    # ── 分段 CRUD ─────────────────────────────────────────────────
    async def insert_segment(self, seg_data: dict[str, Any]) -> None:
        await self.execute_write(
            """INSERT INTO segments
               (segment_id, parent_task_id, content_payload, sequence_order,
                status, tail_context)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                seg_data["segment_id"],
                seg_data["parent_task_id"],
                seg_data["content_payload"],
                seg_data["sequence_order"],
                seg_data.get("status", "PENDING"),
                json.dumps(seg_data.get("tail_context", {}), ensure_ascii=False),
            ),
        )
    async def update_segment_status(
        self,
        segment_id: str,
        status: str,
        result_content: str | None = None,
        output_path: str | None = None,
        new_tail: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        started = now if status == "RUNNING" else None
        completed = now if status in ("COMPLETED", "FAILED") else None
        tail_json = json.dumps(new_tail, ensure_ascii=False) if new_tail else None
        await self.execute_write(
            """UPDATE segments SET status=?, result_content=?, output_path=?,
               tail_context=COALESCE(?, tail_context),
               started_at=COALESCE(?, started_at),
               completed_at=COALESCE(?, completed_at),
               error_message=?
               WHERE segment_id=?""",
            (
                status, result_content, output_path,
                tail_json, started, completed, error_message,
                segment_id,
            ),
        )

    async def update_segment_checkpoint(
        self,
        segment_id: str,
        checkpoint_content: str | None = None,
        checkpoint_tail: dict | None = None,
        checkpoint_summary: str | None = None,
    ) -> None:
        """持久化分段中间结果/尾巴，恢复时不以临时文件作为唯一依据。"""
        await self.execute_write(
            """UPDATE segments SET checkpoint_content=?, checkpoint_tail=?, checkpoint_summary=?
               WHERE segment_id=?""",
            (
                checkpoint_content,
                json.dumps(checkpoint_tail or {}, ensure_ascii=False),
                checkpoint_summary,
                segment_id,
            ),
        )
    async def get_segments_for_task(self, task_id: str) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """SELECT * FROM segments WHERE parent_task_id=?
               ORDER BY sequence_order ASC""",
            (task_id,),
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    async def get_segment(self, segment_id: str) -> dict[str, Any] | None:
        cursor = await self.conn.execute(
            "SELECT * FROM segments WHERE segment_id=?", (segment_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    # ── 聊天记录 CRUD ─────────────────────────────────────────────────
    async def insert_chat_history(self, task_id: str, session_id: str, project_id: str | None, user_query: str, ai_result: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.execute_write(
            """INSERT INTO chat_history
               (task_id, session_id, project_id, user_query, ai_result, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, session_id, project_id, user_query, ai_result, now),
        )

    async def get_chat_history(self, session_id: str) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            "SELECT * FROM chat_history WHERE session_id=? ORDER BY timestamp ASC",
            (session_id,)
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    # ── 模型凭证 CRUD ─────────────────────────────────────────────────
    async def insert_model_credential(self, cred_data: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        import uuid
        cred_id = cred_data.get("id") or str(uuid.uuid4())
        await self.execute_write(
            """INSERT INTO model_credentials
               (id, name, api_endpoint, api_key, model_tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                cred_id,
                cred_data["name"],
                cred_data["api_endpoint"],
                cred_data.get("api_key", ""),
                json.dumps(cred_data.get("model_tags", []), ensure_ascii=False),
                now,
                now
            )
        )

    async def get_all_model_credentials(self) -> list[dict[str, Any]]:
        cursor = await self.conn.execute("SELECT * FROM model_credentials ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            if row_dict.get("model_tags"):
                try:
                    row_dict["model_tags"] = json.loads(row_dict["model_tags"])
                except:
                    row_dict["model_tags"] = []
            # 安全加固：对外返回时对 api_key 做脱敏，避免明文泄露
            if row_dict.get("api_key"):
                row_dict["api_key"] = _mask_secret(row_dict["api_key"])
            results.append(row_dict)
        return results

    async def delete_model_credential(self, cred_id: str) -> None:
        """删除模型凭证（修复：原实现被错误嵌套在 _mask_secret 函数体内，属于死代码）。"""
        await self.execute_write("DELETE FROM model_credentials WHERE id=?", (cred_id,))


def _mask_secret(secret: str) -> str:
    """对敏感字符串进行脱敏：仅保留前4位与后4位。"""
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}...{secret[-4:]}"