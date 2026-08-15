"""
core/db_pool.py — SQLite 连接复用池（性能优化）
================================================
按数据库路径复用 aiosqlite 连接，避免各模块重复 open/close
导致的高并发性能损耗。所有连接默认开启 WAL 模式。

注意：SQLite 为单写多读模型，所有写操作仍经同一连接串行提交；
读多写少场景下本池收益最大。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Dict

import aiosqlite

from core.path_resolver import get_db_path

logger = logging.getLogger(__name__)

# 与 DatabaseManager 保持一致的默认库文件
_DEFAULT_DB_NAME = "tasks.db"


class DatabasePoolManager:
    """按路径索引的连接池（进程内单例）。"""

    _instance: "DatabasePoolManager | None" = None
    _lock = asyncio.Lock()

    def __new__(cls) -> "DatabasePoolManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # 针对每个库路径维护一个空闲连接队列与当前总连接数
            # None 是“已关闭连接占位符”：用于保留池的生命周期计数，
            # 但绝不保留 Windows 上仍锁住临时 SQLite 文件的句柄。
            cls._instance._pools: Dict[str, asyncio.Queue[aiosqlite.Connection | None]] = {}
            cls._instance._conn_counts: Dict[str, int] = {}
            # 不同连接仍然可能同时成为 writer；按数据库路径统一串行化写事务。
            cls._instance._write_locks: Dict[str, asyncio.Lock] = {}
        return cls._instance

    async def _init_pool_if_needed(self, key: str) -> None:
        if key not in self._pools:
            self._pools[key] = asyncio.Queue()
            self._conn_counts[key] = 0
            self._write_locks[key] = asyncio.Lock()

    @asynccontextmanager
    async def write_lock(self, db_path: Path | None = None) -> AsyncIterator[None]:
        """获取指定 SQLite 文件的进程内写锁。

        连接池允许多个读连接，但 SQLite 同一时刻只能有一个可靠的写事务。
        所有 DatabaseManager 的 writer/VACUUM/初始化写入都应使用此锁。
        """
        path = Path(db_path) if db_path else get_db_path(_DEFAULT_DB_NAME)
        key = str(path)
        async with self._lock:
            await self._init_pool_if_needed(key)
            lock = self._write_locks[key]
        async with lock:
            yield

    async def acquire(self, db_path: Path | None = None) -> aiosqlite.Connection:
        """从连接池中获取一个连接（最大 10 个）。如果池空且未达上限则新建。"""
        path = Path(db_path) if db_path else get_db_path(_DEFAULT_DB_NAME)
        key = str(path)
        
        async with self._lock:
            await self._init_pool_if_needed(key)
            
            # 若有空闲连接，直接弹出
            if not self._pools[key].empty():
                conn = self._pools[key].get_nowait()
                if conn is not None:
                    return conn
                # 关闭占位符不代表可复用连接；回收其计数后按正常路径新建。
                self._conn_counts[key] = max(0, self._conn_counts[key] - 1)
                
            # 若没有空闲连接，且未达最大上限(10)，则新建连接
            if self._conn_counts[key] < 10:
                conn = await aiosqlite.connect(str(path))
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA busy_timeout=5000")
                self._conn_counts[key] += 1
                logger.info("创建新数据库连接: %s (当前总数: %d)", path, self._conn_counts[key])
                return conn
                
        # 达最大上限，只能等待空闲连接返回（阻塞外部队列）
        return await self._pools[key].get()

    async def release(
        self,
        conn: aiosqlite.Connection,
        db_path: Path | None = None,
        close_connection: bool = False,
    ) -> None:
        """归还连接到连接池（保持空闲最多 5 个）。"""
        path = Path(db_path) if db_path else get_db_path(_DEFAULT_DB_NAME)
        key = str(path)
        
        async with self._lock:
            await self._init_pool_if_needed(key)
            if close_connection:
                try:
                    await conn.close()
                    # 维持 close() 后的池契约，但不保留文件句柄。
                    self._pools[key].put_nowait(None)
                finally:
                    # 占位符仍计作一个池槽；acquire() 消费占位符时再扣减。
                    pass
                return
            # 若空闲连接数已经超过 5 个，则直接关闭丢弃
            if self._pools[key].qsize() >= 5:
                try:
                    await conn.close()
                except Exception:
                    pass
                self._conn_counts[key] -= 1
            else:
                self._pools[key].put_nowait(conn)

    async def close_all(self) -> None:
        """应用关闭时销毁全部连接。"""
        async with self._lock:
            for key, q in self._pools.items():
                while not q.empty():
                    conn = q.get_nowait()
                    if conn is None:
                        continue
                    try:
                        await conn.close()
                    except Exception:
                        pass
            self._pools.clear()
            self._conn_counts.clear()
            self._write_locks.clear()


# 兼容旧代码的别名入口（原先仅暴露 db_path 固定单连接）
class _LegacyCompat:
    """兼容旧用法 `db_pool.get_connection()` / `db_pool.close()`。"""

    async def get_connection(self) -> aiosqlite.Connection:
        return await db_pool.acquire()

    async def close(self) -> None:
        await db_pool.close_all()


db_pool = DatabasePoolManager()
