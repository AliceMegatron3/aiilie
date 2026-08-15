"""
services/tail_context_manager.py — 尾巴上下文独立管理器（本批次新建）
====================================================================
将原先硬编码在 SegmentPipeline 内部的 tail 传递逻辑解耦为独立模块，
并实现补丁E的"双模式尾巴存储"：

  1. 内存模式（默认/小尾巴）：tail 以 dict 原样保存在 Segment.tail_context 中；
  2. 落盘模式（大尾巴）：tail JSON 体积超过阈值后，序列化写入临时目录，
     内存中仅保存一个极小的 __tail_offload__ 标记 dict，防止 SQLite 与内存膨胀。

对外契约保持向后兼容：
  - Segment.tail_context 字段类型不变（始终是 dict[str, Any]）；
  - 上层只需要调用 materialize() 即可拿到真实尾巴，无感知磁盘/内存切换；
  - 所有路径操作使用 pathlib.Path，兼容 Windows PyInstaller 打包。

另外提供"异常现场尾巴快照"能力：
  - 分段执行抛异常时，将当前尾巴强制落盘（无论阈值），
    供任务重启恢复时读取现场。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from core.path_resolver import get_temp_root

logger = logging.getLogger(__name__)

# 落盘标记键名：出现在 tail dict 顶层时表示尾巴已被卸载到磁盘
OFFLOAD_MARKER_KEY = "__tail_offload__"
# 失败现场快照目录名（位于每个任务的 tails 目录之下）
FAILURE_SNAPSHOT_DIR = "failures"

# 默认阈值：尾巴 JSON 超过 64KB 即落盘
DEFAULT_MEMORY_THRESHOLD_BYTES = 64 * 1024
# 默认 TTL：86400 秒（1 天），过期尾巴可被清理
DEFAULT_TTL_SECONDS = 86400


def _serialized_size_bytes(tail: dict[str, Any]) -> int:
    """计算尾巴序列化为 JSON 后的字节体积。"""
    try:
        payload = json.dumps(tail, ensure_ascii=False)
        return len(payload.encode("utf-8"))
    except (TypeError, ValueError):
        # 不可序列化时按大尾巴处理，避免 DB 写入报错
        return DEFAULT_MEMORY_THRESHOLD_BYTES + 1


class TailContextManager:
    """
    尾巴上下文管理器。

    职责：
    1. 接管尾巴的存取与序列化/反序列化；
    2. 双模式存储：小尾巴内存驻留，大尾巴自动落盘；
    3. TTL 过期标记与清理；
    4. 异常中断场景下失败分段的尾巴现场单独落盘，支持任务恢复。
    """

    def __init__(
        self,
        temp_root: Path | None = None,
        memory_threshold_bytes: int = DEFAULT_MEMORY_THRESHOLD_BYTES,
        enable_disk_offload: bool = True,
        ttl_seconds: int | None = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._temp_root = temp_root or get_temp_root()
        self._tails_root = self._temp_root / "tails"
        self._tails_root.mkdir(parents=True, exist_ok=True)
        self._memory_threshold_bytes = int(memory_threshold_bytes)
        self._enable_disk_offload = bool(enable_disk_offload)
        self._ttl_seconds = ttl_seconds
        logger.info(
            "尾巴管理器初始化: root=%s, 阈值=%d字节, 落盘开关=%s, TTL=%s秒",
            self._tails_root, self._memory_threshold_bytes,
            self._enable_disk_offload, self._ttl_seconds,
        )

    # ── 路径辅助 ────────────────────────────────────────────────

    def _task_dir(self, task_id: str) -> Path:
        """任务专属尾巴目录。task_id 可能含任意字符，做安全清洗。"""
        safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in task_id)
        task_dir = self._tails_root / safe
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    def _tail_file(self, task_id: str, segment_id: str) -> Path:
        return self._task_dir(task_id) / f"{segment_id}_tail.json"

    def _failure_file(self, task_id: str, segment_id: str) -> Path:
        fail_dir = self._task_dir(task_id) / FAILURE_SNAPSHOT_DIR
        fail_dir.mkdir(parents=True, exist_ok=True)
        return fail_dir / f"{segment_id}_failure.json"

    # ── 双模式存取 ──────────────────────────────────────────────

    def store(
        self,
        task_id: str,
        segment_id: str,
        tail: dict[str, Any],
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        """
        保存尾巴，返回可直接写入 Segment.tail_context 的 dict。

        - 落盘开关关闭 或 体积未超阈值：原样返回（内存模式）；
        - 超过阈值：写入磁盘文件，返回 __tail_offload__ 标记 dict（极占内存）；
        - 架构整改 1.1：传入 branch_id 时注入尾巴顶层（若尾巴尚未携带），
          保证平行宇宙分支隔离（"悲剧线"尾巴绝不污染"喜剧线"）。
        """
        if not tail:
            return tail
        if branch_id and "branch_id" not in tail:
            # 拷贝注入，避免污染调用方持有的原 dict
            tail = dict(tail)
            tail["branch_id"] = branch_id
        size = _serialized_size_bytes(tail)
        if not self._enable_disk_offload or size <= self._memory_threshold_bytes:
            logger.debug("尾巴驻留内存: %s/%s (%d字节)", task_id, segment_id, size)
            return tail

        tail_file = self._tail_file(task_id, segment_id)
        try:
            tail_file.write_text(
                json.dumps(tail, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            # 落盘失败兜底：退回内存模式，保证业务不中断
            logger.warning(
                "尾巴落盘失败，回退内存模式: %s - %s", tail_file, exc
            )
            return tail

        marker: dict[str, Any] = {
            OFFLOAD_MARKER_KEY: {
                "path": str(tail_file),
                "size_bytes": size,
                "stored_at": time.time(),
                "ttl_seconds": self._ttl_seconds,
            }
        }
        logger.info(
            "大尾巴已落盘: %s/%s (%d字节) -> %s",
            task_id, segment_id, size, tail_file,
        )
        return marker

    def materialize(self, tail_ref: dict[str, Any] | None) -> dict[str, Any]:
        """
        统一读取入口：把 tail_ref 还原为真实尾巴 dict。

        - 普通 dict：原样返回；
        - 含 __tail_offload__ 标记：从磁盘加载，加载失败则返回空 dict；
        - 返回的永远是新 dict，调用方可安全修改。
        """
        if not tail_ref:
            return {}
        marker = tail_ref.get(OFFLOAD_MARKER_KEY)
        if marker is None:
            return dict(tail_ref)

        # 过期检查
        if self.is_expired(tail_ref):
            logger.warning("尾巴已过期，丢弃: %s", marker.get("path"))
            return {}

        path = Path(marker["path"])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            logger.warning("尾巴文件内容非 dict 结构，返回空: %s", path)
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("尾巴磁盘文件读取失败: %s - %s", path, exc)
            return {}

    def is_expired(self, tail_ref: dict[str, Any]) -> bool:
        """判断落盘尾巴是否超过 TTL。"""
        marker = tail_ref.get(OFFLOAD_MARKER_KEY)
        if marker is None:
            return False
        ttl = marker.get("ttl_seconds")
        if not ttl:
            return False
        stored_at = marker.get("stored_at", 0.0)
        return (time.time() - float(stored_at)) > float(ttl)

    def remove_tail(self, task_id: str, segment_id: str) -> None:
        """删除某分段的落盘尾巴文件（幂等）。"""
        tail_file = self._tail_file(task_id, segment_id)
        try:
            if tail_file.exists():
                tail_file.unlink()
                logger.debug("已删除尾巴文件: %s", tail_file)
        except OSError as exc:
            logger.warning("删除尾巴文件失败: %s - %s", tail_file, exc)

    # ── 异常现场快照 ────────────────────────────────────────────

    def save_failure_snapshot(
        self, task_id: str, segment_id: str, tail: dict[str, Any]
    ) -> Path:
        """
        异常中断场景：把当前失败分段的尾巴强制落盘（无视阈值开关），
        用于后续任务重启恢复现场。
        """
        fail_file = self._failure_file(task_id, segment_id)
        try:
            fail_file.write_text(
                json.dumps(tail or {}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.warning(
                "已保存失败现场尾巴快照: %s/%s -> %s",
                task_id, segment_id, fail_file,
            )
        except OSError as exc:
            logger.error("失败现场快照写入失败: %s - %s", fail_file, exc)
        return fail_file

    def load_failure_snapshot(
        self, task_id: str, segment_id: str
    ) -> dict[str, Any] | None:
        """任务重启恢复时读取失败现场尾巴；不存在则返回 None。"""
        fail_file = self._failure_file(task_id, segment_id)
        if not fail_file.exists():
            return None
        try:
            data = json.loads(fail_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("失败现场快照读取失败: %s - %s", fail_file, exc)
            return None

    def discard_failure_snapshot(self, task_id: str, segment_id: str) -> None:
        """分段恢复执行成功后，删除对应失败快照（幂等）。"""
        fail_file = self._failure_file(task_id, segment_id)
        try:
            if fail_file.exists():
                fail_file.unlink()
                logger.debug("已删除失败现场快照: %s", fail_file)
        except OSError as exc:
            logger.warning("删除失败现场快照失败: %s - %s", fail_file, exc)

    # ── 清理 ────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """
        扫描并清理 TTL 过期的落盘尾巴文件（幂等，返回清理数量）。
        由 TempFileManager 在启动扫描时联动调用。
        """
        removed = 0
        if not self._tails_root.exists():
            return removed
        if not self._ttl_seconds:
            return removed
        cutoff = time.time() - float(self._ttl_seconds)
        # 落盘文件内容是原始尾巴 JSON（不含元数据），
        # 过期判断统一使用文件修改时间 + 全局 TTL。
        for tail_file in self._tails_root.rglob("*_tail.json"):
            try:
                if tail_file.stat().st_mtime < cutoff:
                    tail_file.unlink()
                    removed += 1
            except OSError:
                continue
        if removed:
            logger.info("TTL 过期尾巴清理完成，共清理 %d 个文件", removed)
        return removed
