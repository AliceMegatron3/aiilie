"""
services/result_merger.py — 结果合并器（P0 流式重写）
=====================================================
当 CommandTask 的所有 Segment 完成后，
收集所有分段输出文件并合并为最终结果。
支持多种合并策略：拼接、摘要提取、结构化组装。

P0 修复（本批次）：
  1. ConcatenateStrategy 彻底流式化：不再把分段文本读入内存 list 再 join，
     统一通过分块迭代器逐块写入最终输出文件，百万字级合并 O(1) 内存；
  2. 分段内容读取统一走 resolve_segment_content 语义：
     优先 output_path 磁盘文件，其次 result_content 内存字段；
  3. 最终合并产物保存到临时文件，merge() 返回文件路径而非完整大字符串；
     新增 merge_and_load() 兼容模式：小结果可读入内存返回字符串，
     超过 result_merger.memory_load_threshold_bytes 阈值时返回 None，
     提示上层按文件方式读取。

合并策略接口保持不变，不改动外部调用签名。
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

from core.config_manager import config_manager
from core.path_resolver import get_app_data_dir
from models.task import CommandTask, Segment, SegmentStatus

logger = logging.getLogger(__name__)

# 流式读取分块大小（64KB）
STREAM_CHUNK_SIZE = 64 * 1024
# 默认内存加载阈值：1MB，超过则只返回文件路径
DEFAULT_MEMORY_LOAD_THRESHOLD_BYTES = 1024 * 1024


# ── 分段内容读取辅助（磁盘优先） ───────────────────────────────

def resolve_segment_content(seg: Segment) -> str | None:
    """
    P0 修复：统一的分段结果读取入口。

    优先级：
    1. output_path 指向的磁盘文件存在 → 从磁盘读取（大内容场景）；
    2. result_content 内存/DB 字段 → 直接使用（小内容场景）；
    3. 两者皆无 → None。

    注意：此函数会把内容整体读入内存，仅适合单段读取；
    合并场景请使用 iter_segment_chunks() 流式迭代。
    """
    if seg.output_path:
        p = Path(seg.output_path)
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(
                    "分段 %s 磁盘文件读取失败，回退内存字段: %s - %s",
                    seg.segment_id, p, exc,
                )
    return seg.result_content


def iter_segment_chunks(
    seg: Segment, chunk_size: int = STREAM_CHUNK_SIZE
) -> Iterator[str]:
    """
    P0 修复：分段结果的流式分块迭代器。

    - output_path 文件存在 → 逐块读文件（O(1) 内存）；
    - 否则回退 result_content → 整体 yield 一次。
    """
    if seg.output_path:
        p = Path(seg.output_path)
        if p.exists():
            try:
                with p.open("r", encoding="utf-8") as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
                return
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(
                    "分段 %s 磁盘文件流式读取失败，回退内存字段: %s - %s",
                    seg.segment_id, p, exc,
                )
    if seg.result_content:
        yield seg.result_content


# ── 合并策略接口 ────────────────────────────────────────────────

class MergeStrategy(ABC):
    """结果合并策略抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称。"""
        ...

    @abstractmethod
    async def merge(
        self, segments: list[Segment], task: CommandTask, output_path: Path
    ) -> Path:
        """
        合并多个分段的结果。

        Args:
            segments: 已完成的分段列表（按顺序排列）
            task: 所属的命令任务
            output_path: 要写入的目标文件路径
        Returns:
            合并后的最终结果文件路径
        """
        ...


# ── 内置策略：拼接合并 ──────────────────────────────────────────

class ConcatenateStrategy(MergeStrategy):
    """
    顺序拼接策略（P0 流式重写）。
    逐段、逐块流式写入最终输出文件，任何时刻内存只驻留一个分块。
    """

    @property
    def name(self) -> str:
        return "concatenate"

    async def merge(
        self, segments: list[Segment], task: CommandTask, output_path: Path
    ) -> Path:
        sorted_segs = sorted(segments, key=lambda s: s.sequence_order)
        count = 0
        with output_path.open("w", encoding="utf-8") as f:
            for seg in sorted_segs:
                written = False
                for chunk in iter_segment_chunks(seg):
                    f.write(chunk)
                    written = True
                if written:
                    f.write("\n\n")
                    count += 1

        logger.info(
            "ConcatenateStrategy: 流式合并了 %d 个分段到 %s",
            count, output_path,
        )
        return output_path


# ── 内置策略：结构化 JSON 组装 ──────────────────────────────────

class StructuredStrategy(MergeStrategy):
    """
    结构化组装策略（流式 JSON 转义）。
    将分段结果组装为 JSON 结构，保留分段元数据。
    """

    @property
    def name(self) -> str:
        return "structured"

    @staticmethod
    def _escape_chunk(chunk: str) -> str:
        """JSON 字符串流式转义。"""
        return (
            chunk.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )

    async def merge(
        self, segments: list[Segment], task: CommandTask, output_path: Path
    ) -> Path:
        sorted_segs = sorted(segments, key=lambda s: s.sequence_order)
        with output_path.open("w", encoding="utf-8") as f:
            f.write("{\n")
            f.write(f'  "task_id": "{task.task_id}",\n')
            f.write(f'  "total_segments": {len(sorted_segs)},\n')
            f.write('  "segments": [\n')

            for i, seg in enumerate(sorted_segs):
                f.write('    {\n')
                f.write(f'      "segment_id": "{seg.segment_id}",\n')
                f.write(f'      "sequence_order": {seg.sequence_order},\n')

                tail_json = json.dumps(seg.tail_context, ensure_ascii=False)
                f.write(f'      "tail_context": {tail_json},\n')
                f.write('      "content": "')

                # 流式 JSON 转义，避免整段读入内存
                for chunk in iter_segment_chunks(seg):
                    f.write(self._escape_chunk(chunk))

                f.write('"\n')
                if i < len(sorted_segs) - 1:
                    f.write('    },\n')
                else:
                    f.write('    }\n')

            f.write('  ]\n}')
        logger.info(
            "StructuredStrategy: 流式组装了 %d 个分段的结构化结果",
            len(sorted_segs),
        )
        return output_path


# ── 结果合并器 ──────────────────────────────────────────────────

class ResultMerger:
    """
    结果合并器（P0 流式版）。

    当 CommandTask 的所有 Segment 完成后：
    1. 收集所有分段的输出
    2. 根据策略流式合并为最终结果文件
    3. 返回最终结果文件路径（不再返回完整大字符串）
    """

    def __init__(self) -> None:
        self._strategies: dict[str, MergeStrategy] = {}
        # 注册内置策略
        self.register_strategy(ConcatenateStrategy())
        self.register_strategy(StructuredStrategy())
        # 结果输出目录（使用 pathlib 确保 Windows 路径兼容）
        self._output_dir = get_app_data_dir() / "results"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        # 内存加载阈值（merge_and_load 兼容模式使用）
        self._memory_load_threshold = int(
            config_manager.get(
                "result_merger.memory_load_threshold_bytes",
                DEFAULT_MEMORY_LOAD_THRESHOLD_BYTES,
            )
        )

    def register_strategy(self, strategy: MergeStrategy) -> None:
        """注册合并策略。"""
        self._strategies[strategy.name] = strategy
        logger.debug("已注册合并策略: %s", strategy.name)

    async def merge(
        self,
        task: CommandTask,
        strategy_name: str = "concatenate",
    ) -> Path | str:
        """
        合并任务的所有分段结果（流式，返回文件路径）。

        Args:
            task: 已完成所有分段的命令任务
            strategy_name: 合并策略名称
        Returns:
            合并后的最终结果文件路径（无已完成分段时返回空字符串）
        """
        # 筛选已完成的分段
        completed = [
            s for s in task.segments
            if s.status == SegmentStatus.COMPLETED
        ]

        if not completed:
            logger.warning("任务 %s 没有已完成的分段可合并", task.task_id)
            return ""

        # 选择合并策略
        strategy = self._strategies.get(
            strategy_name,
            self._strategies["concatenate"],
        )

        logger.info(
            "开始合并任务 %s (%d 个分段，策略: %s)",
            task.task_id, len(completed), strategy.name,
        )

        result_file = self._output_dir / f"{task.task_id}_result.txt"

        # 执行合并（流式直接写文件，O(1) 内存）
        result_path = await strategy.merge(completed, task, result_file)

        logger.info(
            "任务 %s 合并完成，结果保存至: %s", task.task_id, result_path
        )

        return result_path

    async def merge_and_load(
        self,
        task: CommandTask,
        strategy_name: str = "concatenate",
        max_bytes: int | None = None,
    ) -> str | None:
        """
        P0 兼容模式：合并后按需读入内存返回字符串。

        - 合并产物字节数 <= max_bytes：返回完整字符串；
        - 超过阈值（或读取失败）：返回 None，上层应改走 merge() 文件路径读取。

        Args:
            task: 已完成所有分段的命令任务
            strategy_name: 合并策略名称
            max_bytes: 内存加载上限，None 时使用配置
                result_merger.memory_load_threshold_bytes（默认 1MB）。

        Returns:
            小结果返回完整字符串；大结果返回 None（文件已落地，见 merge()）。
        """
        if max_bytes is None:
            max_bytes = self._memory_load_threshold

        result = await self.merge(task, strategy_name)
        if isinstance(result, str):
            # 无已完成分段（""）等字符串直接返回
            return result or None

        path = Path(result)
        try:
            if path.stat().st_size > max_bytes:
                logger.info(
                    "合并结果 %d 字节超过内存阈值 %d 字节，返回 None（文件: %s）",
                    path.stat().st_size, max_bytes, path,
                )
                return None
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("合并结果读取失败: %s - %s", path, exc)
            return None
