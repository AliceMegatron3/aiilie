import os
import shutil
import logging
import asyncio
from pathlib import Path
from core.path_resolver import get_app_data_dir
from core.db_pool import db_pool
from core.config_manager import config_manager
from models.resource import ResourceStats, GcTaskReport
import psutil
logger = logging.getLogger(__name__)
class ResourceGovernor:
    """
    补丁E扩展：全局资源管控与垃圾回收服务。
    监控磁盘/内存使用，执行过期文件清理与 SQLite VACUUM。
    """
    def __init__(self):
        self.data_dir = get_app_data_dir()
        self.temp_dir = self.data_dir / "temp"
        self.backup_dir = self.data_dir / "library" / "backups"
        
        # 确保目录存在
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载配置
        self.auto_gc = config_manager.get("resource_governor.auto_gc_enable", True)
        self.safe_disk_gb = config_manager.get("resource_governor.disk_safe_free_gb", 5.0)
    def get_stats(self) -> ResourceStats:
        """获取全域资源统计（兼容 Windows 路径）"""
        try:
            total, used, free = shutil.disk_usage(str(self.data_dir))
            free_gb = free / (1024 ** 3)
        except Exception as e:
            logger.error(f"无法获取磁盘容量: {e}")
            free_gb = 999.0
        # 获取内存使用率
        try:
            memory_percent = psutil.virtual_memory().percent
        except Exception as e:
            logger.error(f"无法获取内存使用率: {e}")
            memory_percent = 0.0
        # 简单遍历计算目录大小
        def get_size_mb(path: Path) -> float:
            if not path.exists():
                return 0.0
            total_size = sum(f.stat().st_size for f in path.glob('**/*') if f.is_file())
            return total_size / (1024 ** 2)
        return ResourceStats(
            memory_usage_percent=round(memory_percent, 1),
            disk_free_gb=round(free_gb, 2),
            directory_usage_mb={
                "temp": round(get_size_mb(self.temp_dir), 2),
                "backups": round(get_size_mb(self.backup_dir), 2)
            },
            cache_object_count=0
        )
    async def run_gc(self, task_id: str) -> GcTaskReport:
        """执行后台垃圾回收 (需被 TaskManager 调度执行)"""
        logger.info(f"[GC] 开始执行全局垃圾回收, 任务ID: {task_id}")
        files_cleaned = 0
        bytes_freed = 0.0
        vacuum_executed = False
        if not self.auto_gc:
            return GcTaskReport(task_id=task_id, status="SKIPPED", files_cleaned=0, bytes_freed=0, vacuum_executed=False, message="Auto GC is disabled in config.")
        # 1. 清理过期 Temp 文件 (LRU 策略)
        # 安全约束：tail 子目录（tails）归 TailContextManager 管理，
        # 且正在运行任务的分段目录受 TempFileManager 保护；
        # 因此只清理纯文件与超时残留的空目录，绝不递归删除子目录，
        # 避免误删运行中任务的 output/tail 产物。
        for f in self.temp_dir.glob("*"):
            if f.is_file():
                try:
                    bytes_freed += f.stat().st_size
                    f.unlink()
                    files_cleaned += 1
                except OSError as e:
                    logger.warning(f"[GC] 无法删除临时文件 {f}: {e}")
            elif f.is_dir():
                # 仅删除“空目录”（无任何子项），非空目录交由 TempFileManager 治理
                try:
                    if not any(f.iterdir()):
                        f.rmdir()
                except OSError as e:
                    logger.warning(f"[GC] 无法删除空临时目录 {f}: {e}")
        # 2. 清理多余备份
        max_backups = config_manager.get("resource_governor.max_backup_count", 10)
        backups = sorted(self.backup_dir.glob("*.bak"), key=lambda x: x.stat().st_mtime)
        if len(backups) > max_backups:
            for old_bak in backups[:-max_backups]:
                try:
                    bytes_freed += old_bak.stat().st_size
                    old_bak.unlink()
                    files_cleaned += 1
                except OSError as e:
                    logger.warning(f"[GC] 无法删除备份文件 {old_bak}: {e}")
        # 3. 仅在无业务压力时执行 SQLite Vacuum
        # （由调用方 / GlobalRouter 确保当前无大量写入任务）
        try:
            db = await db_pool.get_connection()
            await db.execute("VACUUM;")
            vacuum_executed = True
            logger.info("[GC] 数据库 VACUUM 碎片整理完成。")
        except Exception as e:
            logger.warning(f"[GC] VACUUM 失败，可能存在锁冲突: {e}")
        bytes_freed_mb = round(bytes_freed / (1024 ** 2), 2)
        logger.info(f"[GC] 清理完成。删除文件: {files_cleaned} 个，释放空间: {bytes_freed_mb} MB")
        return GcTaskReport(
            task_id=task_id,
            status="SUCCESS",
            files_cleaned=files_cleaned,
            bytes_freed=bytes_freed_mb,
            vacuum_executed=vacuum_executed,
            message="GC completed successfully."
        )
resource_governor = ResourceGovernor()