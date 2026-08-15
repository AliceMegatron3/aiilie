"""
services/system_monitor.py — 系统健康资源探针
===================================================
通过 psutil 与内部心跳探针，构建全系统无死角的资源大盘看板。
输出标准化报表，供最终展示层以及大模型推演性能瓶颈。

P2-2.2 修复：临时目录全量 glob 遍历移出请求路径，改由后台采样协程
周期性执行并缓存；get_health_report() 内不再有全量遍历与阻塞 sleep。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import psutil

from core.path_resolver import get_app_data_dir

logger = logging.getLogger(__name__)

# 健康报告缓存 TTL（秒）：避免每次请求都阻塞采集 CPU
_REPORT_CACHE_TTL = 5.0
# 临时目录后台采样间隔（秒）：全量 glob 只在后台协程执行
_TEMP_SAMPLE_INTERVAL = 60.0


class SystemMonitor:
    """系统各项硬软件指标采集器"""

    def __init__(self) -> None:
        # SQLite 探活目标：从 path_resolver 获取真实数据库路径。
        # 修复：原先硬编码 database.db 与核心 tasks.db 不一致，
        # 导致 DB 心跳永远失败（文件不存在）。
        from core.path_resolver import get_db_path
        self.db_path = get_db_path()
        self._is_quantizing = False
        # 报告缓存（性能优化：避免事件循环内阻塞采样）
        self._report_cache: dict[str, Any] | None = None
        self._cache_ts = 0.0
        # P2-2.2：临时目录大小后台采样缓存（请求路径零遍历）
        self._temp_size_cache: dict[str, Any] = {"mb": 0.0, "ts": 0.0}
        self._bg_sampler_task: asyncio.Task | None = None

    # ── P2-2.2：后台采样协程 ────────────────────────────────────

    def start_background_sampler(self) -> None:
        """启动后台采样协程（幂等）。由 bootstrap 在事件循环内调用。"""
        if self._bg_sampler_task is not None and not self._bg_sampler_task.done():
            return
        self._bg_sampler_task = asyncio.create_task(self._sampler_loop())
        logger.info("[SystemMonitor] 后台采样协程已启动（临时目录每 %.0fs 刷新）", _TEMP_SAMPLE_INTERVAL)

    async def _sampler_loop(self) -> None:
        while True:
            try:
                self._sample_temp_size()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[SystemMonitor] 后台采样异常: %s", exc)
            await asyncio.sleep(_TEMP_SAMPLE_INTERVAL)

    def _sample_temp_size(self) -> None:
        """全量遍历临时目录统计体积（仅后台协程调用）。"""
        try:
            from core.path_resolver import get_temp_root
            temp_dir = get_temp_root()
            temp_size_mb = 0.0
            if temp_dir.exists():
                temp_size = sum(
                    f.stat().st_size for f in temp_dir.glob("**/*") if f.is_file()
                )
                temp_size_mb = temp_size / (1024 ** 2)
            self._temp_size_cache = {"mb": temp_size_mb, "ts": time.monotonic()}
        except Exception as exc:
            logger.warning("[SystemMonitor] 临时目录采样失败: %s", exc)

    async def stop_background_sampler(self) -> None:
        if self._bg_sampler_task is not None:
            self._bg_sampler_task.cancel()
            try:
                await self._bg_sampler_task
            except (asyncio.CancelledError, Exception):
                pass
            self._bg_sampler_task = None

    # ── 探针与报告 ──────────────────────────────────────────────

    def acquire_quantization_lock(self) -> bool:
        """获取量化算力锁"""
        if self._is_quantizing:
            return False
        self._is_quantizing = True
        logger.warning("[SystemMonitor] 【算力霸权】量化锁已开启，所有大模型创作通道被挂起！")
        return True
        
    def release_quantization_lock(self) -> None:
        """释放量化算力锁"""
        self._is_quantizing = False
        logger.info("[SystemMonitor] 【算力霸权】量化锁已释放，创作通道恢复。")
        
    def is_locked_for_quantization(self) -> bool:
        return self._is_quantizing
        
    def get_health_report(self) -> dict[str, Any]:
        """一次性采集 CPU、内存、磁盘以及依赖组件健康度。

        P2-2.2 修复：
        - CPU 用 interval=None（非阻塞瞬时/上次采样值，不再 sleep 0.1s）；
        - 临时目录体积读后台采样缓存（后台协程未启动时首次同步采样并长 TTL 防抖）；
        - 报告整体缓存 _REPORT_CACHE_TTL 秒。
        """
        now = time.monotonic()
        if self._report_cache is not None and now - self._cache_ts < _REPORT_CACHE_TTL:
            return self._report_cache

        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage(str(Path.home().anchor))  # 检查系统盘/主盘剩余
            
            # 临时目录体积：优先后台采样缓存；未就绪（如独立测试）同步采样一次
            if now - self._temp_size_cache["ts"] > _TEMP_SAMPLE_INTERVAL * 2:
                self._sample_temp_size()
            temp_size_mb = round(self._temp_size_cache.get("mb", 0.0), 2)

            report = {
                "resource": {
                    "cpu_percent": round(cpu_percent, 1),
                    "memory_percent": round(mem.percent, 1),
                    "memory_available_mb": round(mem.available / (1024 ** 2), 1),
                    "disk_free_gb": round(disk.free / (1024 ** 3), 1),
                    "temp_dir_mb": temp_size_mb
                },
                "services": {
                    "sqlite_health": self._check_db_health(),
                    "api_router_health": True,  # 只要能响应当前请求，基本代表正常
                    "is_quantizing": self._is_quantizing
                }
            }
            self._report_cache = report
            self._cache_ts = now
            return report
        except Exception as e:
            logger.error("[SystemMonitor] 采集健康指标崩溃: %s", e)
            return {"status": "error", "message": str(e)}
            
    def _check_db_health(self) -> bool:
        """极简 SQL 探活，判断底层数据 IO 是否被死锁阻塞。"""
        if not self.db_path.exists():
            return False
            
        try:
            with sqlite3.connect(self.db_path, timeout=1.0) as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.warning("[SystemMonitor] DB 心跳检测失败: %s", e)
            return False
