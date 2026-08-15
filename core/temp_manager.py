"""
core/temp_manager.py — 全局/局部临时工作区管理器 (Phase 10)
===================================================
提供严格按书籍、项目、功能作用域隔离的临时文件目录。
支持任务结束时的精确清理。
"""
import shutil
import logging
import uuid
import os
import threading
from pathlib import Path
from core.path_resolver import get_app_data_dir, get_temp_root

logger = logging.getLogger(__name__)

class ScopedTempManager:
    def __init__(self):
        # 修复：原先基于 get_app_data_dir().parent（%APPDATA%/No0_AI_V4）
        # 导致发散引擎临时文件落在 %APPDATA%/No0_AI_V4/temp，
        # 与主链路 services/temp_file_manager 的 %APPDATA%/No0_AI_V4/data/temp
        # 两套体系并存，健康报告与 GC 清理互相不可见。
        # 统一收敛到标准临时根目录（get_temp_root）下的 scoped 子域。
        self.base_dir = get_temp_root()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # workspace 引用标记：GC 只能回收明确不在运行中的工作区。
        self._active_workspaces: set[Path] = set()
        self._workspace_lock = threading.RLock()
        
    def _get_scoped_dir(self, book_id: str = None, function_type: str = "global") -> Path:
        """根据书籍ID和功能域返回对应的物理路径（统一在标准临时根下）"""
        if book_id:
            # 书籍专属临时工作区
            return self.base_dir / "scoped" / book_id / function_type
        else:
            # 全局功能临时工作区
            return self.base_dir / "scoped" / function_type

    def create_workspace(self, book_id: str = None, function_type: str = "global", task_id: str = None) -> Path:
        """
        创建一个临时的物理工作区，返回路径。
        """
        task_id = task_id or uuid.uuid4().hex[:8]
        target_dir = self._get_scoped_dir(book_id, function_type) / task_id
        target_dir.mkdir(parents=True, exist_ok=True)
        with self._workspace_lock:
            self._active_workspaces.add(target_dir)
            (target_dir / ".active").write_text(str(os.getpid()), encoding="ascii")
        logger.info(f"[TempManager] 创建局部临时空间: {target_dir}")
        return target_dir

    def _is_workspace_active(self, workspace_path: Path) -> bool:
        with self._workspace_lock:
            if workspace_path in self._active_workspaces:
                return True
        marker = workspace_path / ".active"
        if not marker.exists():
            return False
        try:
            pid = int(marker.read_text(encoding="ascii").strip())
            if pid == os.getpid():
                return True
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    def cleanup_workspace(self, workspace_path: Path) -> bool:
        """
        清理指定的临时物理空间。
        """
        if self._is_workspace_active(workspace_path):
            logger.info(f"[TempManager] 跳过运行中临时空间: {workspace_path}")
            return False
        if workspace_path.exists() and workspace_path.is_dir():
            try:
                with self._workspace_lock:
                    self._active_workspaces.discard(workspace_path)
                shutil.rmtree(workspace_path)
                logger.info(f"[TempManager] 已清理临时空间: {workspace_path}")
                return True
            except Exception as e:
                logger.error(f"[TempManager] 清理临时空间失败 {workspace_path}: {e}")
                return False
        return False

    def cleanup_orphans(self, function_type: str = "divergence", max_age_seconds: int = 3600) -> int:
        """清理超过 max_age_seconds 未修改的孤儿 workspace 目录（返回清理数量）。

        针对发散引擎等「只建不清理」的场景，按 mtime 阈值回收过期的
        scoped/<function_type>/<task_id> 目录，防止临时文件无界累积。
        """
        import time

        scoped_root = self.base_dir / "scoped" / function_type
        if not scoped_root.exists():
            return 0
        cleaned = 0
        now = time.time()
        for child in scoped_root.iterdir():
            if not child.is_dir():
                continue
            try:
                # 正在运行的任务拥有 .active 引用标记，GC 不得删除。
                if now - child.stat().st_mtime > max_age_seconds and not self._is_workspace_active(child):
                    shutil.rmtree(child, ignore_errors=True)
                    cleaned += 1
                    logger.info(f"[TempManager] 回收过期临时空间: {child}")
            except OSError:
                continue
        return cleaned

# 单例
temp_manager = ScopedTempManager()
