"""
guards/fs_guard.py — 全局文件系统并发守卫（多读单写）
===================================================
架构一致性整改 1.2：将原先的互斥锁升级为**多读单写读写锁**。

设计要点：
  1. 每个绝对文件路径对应一个 _FileGate（读者计数 + 写者等待标志 + Condition）；
  2. 读操作（lock_file_read / lock_file(mode="read")）并发共享，互不阻塞；
  3. 写操作独占：等待全部读者退出后进入，且持有期间新读者一律排队（写优先，防写者饥饿）；
  4. 对外 API 向后兼容：`lock_file(path)` 默认仍是**写锁**（原语义不变），
     上层既有调用代码无需任何改动；需要读并发时改用 `lock_file_read`。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


class _FileGate:
    """单文件路径的读写门：读者计数 + 写者等待标志 + 条件变量。"""

    def __init__(self) -> None:
        self.readers: int = 0
        self.writer_waiting: bool = False
        self.cond = asyncio.Condition()


class FileSystemGuard:
    """基于文件绝对路径级别的并发访问门神（多读单写）。"""

    def __init__(self) -> None:
        self._gates: dict[str, _FileGate] = {}
        self._dict_lock = asyncio.Lock()

    async def _get_gate(self, file_path: str) -> _FileGate:
        """获取指定文件的专属门。路径需由调用方标准化（Path(...).resolve().as_posix()）。"""
        async with self._dict_lock:
            if file_path not in self._gates:
                self._gates[file_path] = _FileGate()
            return self._gates[file_path]

    @asynccontextmanager
    async def lock_file(
        self, file_path: str, mode: str = "write"
    ) -> AsyncGenerator[None, None]:
        """
        以 Async Context Manager 形式提供文件锁。

        - mode="write"（默认，向后兼容旧行为）：独占写锁，等待所有读者退出；
          持有期间新读者必须排队（写优先）。
        - mode="read"：共享读锁，多读者并发；若有写者等待/持有则排队。

        批次 1/2/3 的关键 IO 写入保持 `async with guard.lock_file(path):` 不变。
        """
        gate = await self._get_gate(file_path)

        if mode == "read":
            async with gate.cond:
                await gate.cond.wait_for(lambda: not gate.writer_waiting)
                gate.readers += 1
                logger.debug("[FileSystemGuard] 获取读锁 (读者数=%d): %s", gate.readers, file_path)
            try:
                yield
            finally:
                async with gate.cond:
                    gate.readers -= 1
                    if gate.readers == 0:
                        gate.cond.notify_all()
                    logger.debug("[FileSystemGuard] 释放读锁 (读者数=%d): %s", gate.readers, file_path)
            return

        # ── 写锁（独占，写优先） ──
        async with gate.cond:
            gate.writer_waiting = True
            try:
                await gate.cond.wait_for(lambda: gate.readers == 0)
                logger.debug("[FileSystemGuard] 获取写锁（独占）: %s", file_path)
                yield
            finally:
                gate.writer_waiting = False
                gate.cond.notify_all()
                logger.debug("[FileSystemGuard] 释放写锁: %s", file_path)

    @asynccontextmanager
    async def lock_file_read(self, file_path: str) -> AsyncGenerator[None, None]:
        """便捷入口：共享读锁（大量并发读取互不阻塞）。"""
        async with self.lock_file(file_path, mode="read"):
            yield

    # ── 兼容别名：部分旧调用方期望 acquire/release 语义 ─────────────
    async def acquire_lock(self, file_path: str, mode: str = "write") -> None:
        """
        兼容旧 API 风格的手动加锁入口。
        注意：asyncio 场景下推荐使用 `async with lock_file(...)`；
        手动加锁必须与 release_lock 严格配对，且异常路径需自行兜底。
        """
        gate = await self._get_gate(file_path)
        holders: dict[str, str] = getattr(self, "_manual_holder", {})
        async with gate.cond:
            if mode == "read":
                await gate.cond.wait_for(lambda: not gate.writer_waiting)
                gate.readers += 1
            else:
                gate.writer_waiting = True
                await gate.cond.wait_for(lambda: gate.readers == 0)
            holders[file_path] = mode
        self._manual_holder = holders

    async def release_lock(self, file_path: str) -> None:
        """兼容旧 API 风格的释放入口（与 acquire_lock 配对）。"""
        holders: dict[str, str] = getattr(self, "_manual_holder", {})
        mode = holders.pop(file_path, None)
        self._manual_holder = holders
        if mode is None:
            logger.warning("[FileSystemGuard] release_lock 无匹配的 acquire: %s", file_path)
            return
        gate = self._gates.get(file_path)
        if gate is None:
            return
        async with gate.cond:
            if mode == "read":
                gate.readers -= 1
            else:
                gate.writer_waiting = False
            gate.cond.notify_all()
