"""
services/temp_file_manager.py — 临时文件生命周期管理
=====================================================
为每个 Segment 分配独立的临时目录。
支持标记文件为"持久化"或"临时"，防止磁盘空间泄漏。

补丁E增强（本批次新增）：
  1. 磁盘配额：超过 task.temp_max_size_gb 上限后，按 LRU 淘汰已完成任务的临时文件；
  2. 启动扫描清理：程序启动时自动扫描临时目录，清理 orphaned 遗留目录；
     正在运行任务对应的目录禁止删除（protected 集合）；
  3. 与 TailContextManager 联动：tails 子目录由尾巴管理器单独管理，扫描时跳过。

所有路径操作使用 pathlib.Path，确保 Windows 兼容性。
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

from core.path_resolver import get_app_data_dir, get_temp_root

logger = logging.getLogger(__name__)

# tails 子目录由 TailContextManager 管理，启动扫描时跳过
RESERVED_DIR_NAMES = {"tails"}


class TempFileManager:
    """
    临时文件生命周期管理器。

    职责：
    1. 为每个 Segment 分配独立的临时目录
    2. 跟踪"临时"和"持久化"文件
    3. 任务结束时自动清理临时文件
    4. 磁盘配额控制 + LRU 淘汰（补丁E）
    5. 启动时孤儿目录扫描清理（补丁E）
    """

    def __init__(
        self,
        temp_root: Path | None = None,
        max_size_bytes: int | None = None,
        cleanup_orphan_on_start: bool = True,
    ) -> None:
        # 使用 path_resolver 提供的标准临时目录
        self._temp_root = temp_root or get_temp_root()
        # 持久化文件存储目录（不随任务结束而删除）
        self._persistent_root = get_app_data_dir() / "persistent"
        self._persistent_root.mkdir(parents=True, exist_ok=True)
        # 跟踪已标记为持久化的文件路径
        self._persistent_files: set[Path] = set()
        # 跟踪已分配的分段目录
        self._segment_dirs: dict[str, Path] = {}
        # ── 补丁E：配额与 LRU ────────────────────────────────
        # 磁盘配额上限（字节），None 表示不限制
        self._max_size_bytes = max_size_bytes
        # 启动时是否清理孤儿目录
        self._cleanup_orphan_on_start = cleanup_orphan_on_start
        # LRU 元数据：segment_id -> 最近访问时间戳 / 目录体积
        self._last_access: dict[str, float] = {}
        self._dir_sizes: dict[str, int] = {}
        # 运行中任务保护：task_id -> segment_ids
        self._active_task_segments: dict[str, list[str]] = {}
        logger.info(
            "临时文件管理器初始化: temp=%s, persistent=%s, 配额=%s字节, 启动清理=%s",
            self._temp_root, self._persistent_root,
            self._max_size_bytes, self._cleanup_orphan_on_start,
        )

    # ── 运行中任务保护（LRU/启动扫描共用） ──────────────────────

    def register_active_task(self, task_id: str, segment_ids: list[str]) -> None:
        """登记运行中任务及其分段目录，防止被 LRU 淘汰或启动扫描误删。"""
        self._active_task_segments[task_id] = list(segment_ids)
        logger.debug("登记运行中任务保护: %s (%d个分段)", task_id, len(segment_ids))

    def unregister_active_task(self, task_id: str) -> None:
        """任务结束（含失败/取消）后解除保护。"""
        self._active_task_segments.pop(task_id, None)
        logger.debug("解除任务保护: %s", task_id)

    def _protected_segment_ids(self) -> set[str]:
        """汇总当前全部运行中任务的分段 ID 集合。"""
        protected: set[str] = set()
        for seg_ids in self._active_task_segments.values():
            protected.update(seg_ids)
        return protected

    def _is_segment_protected(self, segment_id: str) -> bool:
        """清理前再次校验引用，避免 GC 与任务结束清理竞态。"""
        return any(segment_id in ids for ids in self._active_task_segments.values())

    # ── 目录分配 ──────────────────────────────────────────────

    def get_segment_dir(self, segment_id: str) -> Path:
        """
        获取分段专属的临时目录，不存在则自动创建。

        目录结构: {temp_root}/{segment_id}/
        使用 pathlib.Path 确保 Windows 路径规范。
        """
        if segment_id in self._segment_dirs:
            self._last_access[segment_id] = time.time()
            return self._segment_dirs[segment_id]

        seg_dir = self._temp_root / segment_id
        seg_dir.mkdir(parents=True, exist_ok=True)
        self._segment_dirs[segment_id] = seg_dir
        self._last_access[segment_id] = time.time()
        logger.debug("分配临时目录: %s -> %s", segment_id, seg_dir)

        # 分配新目录后检查配额，必要时触发 LRU 淘汰
        self._enforce_quota()
        return seg_dir

    def mark_persistent(self, file_path: Path) -> Path:
        """
        将文件标记为"持久化"。
        文件将被复制到持久化目录，不随任务结束而删除。

        Returns:
            持久化后的新文件路径
        """
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 保持相对路径结构
        dest = self._persistent_root / file_path.name
        # 如果目标已存在，添加序号避免覆盖
        counter = 1
        original_stem = dest.stem
        while dest.exists():
            dest = dest.with_stem(f"{original_stem}_{counter}")
            counter += 1

        shutil.copy2(str(file_path), str(dest))
        self._persistent_files.add(dest)
        logger.info("文件已标记为持久化: %s -> %s", file_path, dest)
        return dest

    # ── 补丁E：磁盘配额 + LRU 淘汰 ─────────────────────────────

    def _dir_size_bytes(self, seg_dir: Path) -> int:
        """统计分段目录的总字节数（失败返回 0）。"""
        try:
            if not seg_dir.exists():
                return 0
            total = 0
            for f in seg_dir.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
            return total
        except OSError:
            return 0

    def _total_tracked_bytes(self) -> int:
        """当前受管临时目录的总体积。"""
        return sum(self._dir_size_bytes(d) for d in self._segment_dirs.values())

    def _enforce_quota(self) -> None:
        """
        磁盘配额检查：总占用超过上限时，按 LRU 顺序淘汰
        "已完成任务"的临时目录，直到回到配额内。
        正在运行任务（protected）与持久化文件不受影响。
        """
        if self._max_size_bytes is None:
            return

        protected = self._protected_segment_ids()
        while True:
            total = self._total_tracked_bytes()
            if total <= self._max_size_bytes:
                return
            # 找出最久未访问且不受保护的目录
            evictable = [
                (self._last_access.get(sid, 0.0), sid, sdir)
                for sid, sdir in self._segment_dirs.items()
                if sid not in protected
            ]
            if not evictable:
                logger.warning(
                    "磁盘配额超限（%.2fMB > %.2fMB），但无可淘汰目录（全部受保护）",
                    total / (1024 * 1024), self._max_size_bytes / (1024 * 1024),
                )
                return
            _, sid, sdir = min(evictable, key=lambda item: item[0])
            logger.warning(
                "触发 LRU 淘汰: 分段 %s (%s)，当前占用 %.2fMB",
                sid, sdir, total / (1024 * 1024),
            )
            self.cleanup_segment(sid)

    # ── 清理 ──────────────────────────────────────────────────

    def cleanup_segment(self, segment_id: str) -> None:
        """
        清理指定分段的临时目录。
        已标记为持久化的文件不会被删除。

        安全保护（P0 修复）：cleanup_task 仅在任务正常结束后调用，
        但若 output_path 恰好指向本临时目录（DB 大结果外置场景），
        直接删除会连带销毁用户已看到的结果文件。
        因此删除前校验：目录中不存在 result_available 标记文件时才能整体删除；
        否则仅移除目录跟踪，保留磁盘数据（由配额 LRU 按访问时间自然淘汰）。
        """
        if self._is_segment_protected(segment_id):
            logger.info("跳过运行中任务分段清理: %s", segment_id)
            return
        seg_dir = self._segment_dirs.get(segment_id)
        if seg_dir and seg_dir.exists():
            # 保护已产出 output 文件的目录：output_*.txt 存在视为会话仍需读取
            if any(p.name.startswith("output_") for p in seg_dir.iterdir() if p.is_file()):
                logger.info(
                    "分段 %s 目录含 output 产物文件，跳过物理删除（仅解除跟踪）",
                    segment_id,
                )
                self._segment_dirs.pop(segment_id, None)
                self._last_access.pop(segment_id, None)
                self._dir_sizes.pop(segment_id, None)
                return
            try:
                shutil.rmtree(str(seg_dir))
                logger.info("已清理分段临时目录: %s", seg_dir)
            except OSError as exc:
                # Windows 下可能因文件锁定而失败
                logger.warning(
                    "清理分段目录失败 (可能被锁定): %s - %s", seg_dir, exc
                )
            finally:
                self._segment_dirs.pop(segment_id, None)
                self._last_access.pop(segment_id, None)
                self._dir_sizes.pop(segment_id, None)

    def cleanup_task(self, task_id: str, segment_ids: list[str]) -> None:
        """
        清理整个任务的所有临时文件。
        
        补丁: 任务完成或失败后 10 分钟删除临时缓存文件
        """
        import asyncio
        async def _delayed_cleanup():
            logger.info("任务 %s 将在 10 分钟后清理临时文件 (%d 个分段)", task_id, len(segment_ids))
            await asyncio.sleep(600)
            # 任务可能在延迟窗口内被重新恢复/重新登记；不能让旧清理任务解除新引用。
            if self._active_task_segments.get(task_id) != list(segment_ids):
                logger.info("任务 %s 已重新登记，跳过过期临时文件清理", task_id)
                return
            # 任务已完成/取消，先解除引用再物理清理；否则 cleanup_segment 会正确地
            # 把它当作运行中资源而跳过，导致完成任务永不回收。
            self.unregister_active_task(task_id)
            for sid in segment_ids:
                self.cleanup_segment(sid)
            logger.info("任务 %s 临时文件清理完成", task_id)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_delayed_cleanup())
        except RuntimeError:
            # Fallback if no running loop
            # 与异步延迟路径保持一致：先解除任务引用，再执行物理清理，
            # 否则 cleanup_segment 会把资源误判为运行中并跳过删除。
            self.unregister_active_task(task_id)
            for sid in segment_ids:
                self.cleanup_segment(sid)

    def cleanup_all(self) -> None:
        """
        清理所有临时文件（用于系统关闭时）。
        持久化文件不受影响。
        """
        logger.info("开始全局临时文件清理")
        for sid in list(self._segment_dirs.keys()):
            if not self._is_segment_protected(sid):
                self.cleanup_segment(sid)
        # 清理可能残留的空目录
        if self._temp_root.exists():
            for child in self._temp_root.iterdir():
                if child.is_dir() and not any(child.iterdir()):
                    try:
                        child.rmdir()
                    except OSError:
                        pass
        logger.info("全局临时文件清理完成")

    def startup_cleanup(
        self,
        protected_segment_ids: set[str] | None = None,
    ) -> int:
        """
        补丁E：程序启动时扫描临时目录，清理 orphaned 遗留目录。

        Args:
            protected_segment_ids: 需要保护的分段 ID 集合
                （如数据库中 PENDING/RUNNING 任务的片段目录）。

        Returns:
            清理的孤儿目录数量。
        """
        if not self._cleanup_orphan_on_start:
            logger.info("启动孤儿扫描已通过配置关闭，跳过")
            return 0

        protected = set(protected_segment_ids or set())
        protected |= self._protected_segment_ids()

        removed = 0
        if not self._temp_root.exists():
            return removed

        for child in self._temp_root.iterdir():
            if not child.is_dir():
                continue
            # 跳过保留目录（如 tails，由 TailContextManager 管理）
            if child.name in RESERVED_DIR_NAMES:
                continue
            # 跳过受保护的分段目录
            if child.name in protected:
                logger.debug("启动扫描保护目录: %s", child)
                continue
            try:
                shutil.rmtree(str(child))
                removed += 1
                logger.warning("启动扫描清理孤儿目录: %s", child)
            except OSError as exc:
                logger.warning("孤儿目录清理失败: %s - %s", child, exc)

        logger.info("启动孤儿目录扫描完成，清理 %d 个遗留目录", removed)
        return removed

    # ── 统计 ──────────────────────────────────────────────────

    def get_disk_usage(self) -> dict[str, Any]:
        """获取临时目录的磁盘使用统计（含补丁E配额信息）。"""
        total_size = 0
        file_count = 0
        if self._temp_root.exists():
            for f in self._temp_root.rglob("*"):
                if f.is_file():
                    total_size += f.stat().st_size
                    file_count += 1
        max_mb = (
            round(self._max_size_bytes / (1024 * 1024), 2)
            if self._max_size_bytes is not None else None
        )
        return {
            "temp_root": str(self._temp_root),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "file_count": file_count,
            "segment_count": len(self._segment_dirs),
            "persistent_file_count": len(self._persistent_files),
            # ── 补丁E 配额字段 ──
            "quota_max_mb": max_mb,
            "quota_used_percent": (
                round(total_size / self._max_size_bytes * 100, 2)
                if self._max_size_bytes else None
            ),
            "active_task_count": len(self._active_task_segments),
        }
