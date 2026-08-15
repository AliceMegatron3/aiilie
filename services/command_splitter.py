"""
services/command_splitter.py — 智能分段拆解器
===============================================
根据指令类型和长度，自动决定是否分段。
设计 SplitStrategy 接口，允许未来注册不同的拆分算法。

补丁（短指令高负载强制分段，向后完全兼容）：
  - segment_strategy 新增两个控制字面量：
      * force_split：强制分段——跳过字符数/关键词判断，短指令（<2000字符）
        基于任务元信息做逻辑分片，产出 2-3 个有序 Segment（初始化空 tail）；
        长文本仍走既有拆分算法保持粒度；
      * no_split：强制不拆分，永远只生成 1 个 Segment；
      * auto：默认值，原有字符阈值+关键词判断逻辑 100% 保留，存量任务行为不变。
  - 所有路径输出日志标记 [SPLIT_STRATEGY:{strategy}]，便于线上排查。
"""
from __future__ import annotations

import logging
import math
import uuid
from abc import ABC, abstractmethod
from typing import Any

from models.task import CommandTask, Segment, normalize_segment_strategy

logger = logging.getLogger(__name__)

# ── 默认阈值 ─────────────────────────────────────────────────────
DEFAULT_CHAR_THRESHOLD = 2000  # 超过此字数触发分段
DEFAULT_CHUNK_SIZE = 1500       # 每段最大字数
# 专项2修复：预算约束最小 chunk（防无限细分）
CHAR_BUDGET_MIN_CHUNK = 256

# ── 补丁：强制分段控制常量 ──────────────────────────────────────
SEGMENT_STRATEGY_FORCE_SPLIT = "force_split"
SEGMENT_STRATEGY_NO_SPLIT = "no_split"
SEGMENT_STRATEGY_AUTO = "auto"
# 短指令逻辑分片：最多产出的分段数（验收要求 2-3 段）
FORCE_SPLIT_MAX_CHUNKS = 3
# 低于该长度视为"极短指令"，无法按文本切分，走逻辑分片
FORCE_SPLIT_MIN_MEANINGFUL_LEN = 30

# 触发分段的关键词列表（小说创作相关）
SPLIT_TRIGGER_KEYWORDS: list[str] = [
    "生成全书大纲",
    "创建完整大纲",
    "撰写全书",
    "生成章节",
    "批量生成",
    "全文改写",
    "complete outline",
]


# ── 策略接口 ─────────────────────────────────────────────────────

class SplitStrategy(ABC):
    """分段策略抽象基类，允许注册不同的拆分算法。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称，用于 segment_strategy 字段匹配。"""
        ...

    @abstractmethod
    def split(self, task: CommandTask) -> list[Segment]:
        """
        将 CommandTask 拆解为 Segment 列表。
        
        Args:
            task: 待拆解的命令任务
        Returns:
            Segment 对象列表（至少1个）
        """
        ...


# ── 内置策略：按字数拆分 ─────────────────────────────────────────

class ByLengthStrategy(SplitStrategy):
    """按字数拆分策略。"""

    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        self._chunk_size = chunk_size

    @property
    def name(self) -> str:
        return "by_length"

    def split(self, task: CommandTask) -> list[Segment]:
        text = task.raw_command
        segments: list[Segment] = []
        offset = 0
        order = 0

        while offset < len(text):
            # 尝试在句号、换行等自然断点处切割
            end = min(offset + self._chunk_size, len(text))
            if end < len(text):
                # 向后搜索最近的自然断句点
                for sep in ["\n\n", "\n", "。", ". ", "；", "; "]:
                    last_sep = text.rfind(sep, offset, end)
                    if last_sep > offset:
                        end = last_sep + len(sep)
                        break

            chunk = text[offset:end]
            
            prev_tail = None
            if offset > 0:
                prev_tail = text[max(0, offset - 50):offset]
            next_head = None
            if end < len(text):
                next_head = text[end:min(len(text), end + 50)]

            seg = Segment(
                segment_id=f"seg_{uuid.uuid4().hex[:12]}",
                parent_task_id=task.task_id,
                content_payload=chunk,
                sequence_order=order,
                tail_context={},
                prev_tail=prev_tail,
                next_head=next_head,
            )
            segments.append(seg)
            offset = end
            order += 1

        logger.info(
            "ByLengthStrategy: 任务 %s 被拆分为 %d 个分段",
            task.task_id, len(segments),
        )
        return segments


# ── 内置策略：按章节标记拆分 ─────────────────────────────────────

class ByChapterStrategy(SplitStrategy):
    """按章节标记拆分（适用于含"第X章"等标记的文本）。"""

    @property
    def name(self) -> str:
        return "by_chapter"

    def split(self, task: CommandTask) -> list[Segment]:
        import re

        text = task.raw_command
        # 匹配"第X章"、"Chapter X"等模式
        pattern = re.compile(r"(?=(?:第[一二三四五六七八九十百千\d]+章|Chapter\s+\d+))", re.IGNORECASE)
        parts = pattern.split(text)
        parts = [p.strip() for p in parts if p.strip()]

        if not parts:
            parts = [text]

        segments: list[Segment] = []
        for i, part in enumerate(parts):
            seg = Segment(
                segment_id=f"seg_{uuid.uuid4().hex[:12]}",
                parent_task_id=task.task_id,
                content_payload=part,
                sequence_order=i,
                tail_context={},
            )
            segments.append(seg)

        logger.info(
            "ByChapterStrategy: 任务 %s 被拆分为 %d 个分段",
            task.task_id, len(segments),
        )
        return segments


# ── 智能分段拆解器 ───────────────────────────────────────────────

class CommandSplitter:
    """
    智能分段拆解器。
    
    根据 task.segment_strategy 选择对应的拆分策略。
    当策略为 'auto' 时，自动检测是否需要分段。
    支持通过 register_strategy() 注册自定义策略。
    """

    def __init__(self) -> None:
        self._strategies: dict[str, SplitStrategy] = {}
        # 注册内置策略
        self.register_strategy(ByLengthStrategy())
        self.register_strategy(ByChapterStrategy())
        # 批次7：深度思考按阶段拆分策略（feature 开关门控注册，
        # 关闭时 deep_think 策略不存在，任务回退 auto 保持旧行为）
        try:
            from core.config_manager import config_manager

            if config_manager.get_bool("feature.deep_thinking_enable", False):
                from strategies.deep_think_splitter import DeepThinkSplitter

                self.register_strategy(DeepThinkSplitter())
        except Exception as exc:  # 注册失败不阻断启动
            logger.warning("[CommandSplitter] 深度思考策略注册失败: %s", exc)

    def register_strategy(self, strategy: SplitStrategy) -> None:
        """注册一个新的分段策略。"""
        self._strategies[strategy.name] = strategy
        logger.debug("已注册分段策略: %s", strategy.name)

    def _needs_splitting(self, task: CommandTask) -> bool:
        """判断任务是否需要分段处理。"""
        text = task.raw_command
        # 条件1：字数超过阈值
        if len(text) > DEFAULT_CHAR_THRESHOLD:
            return True
        # 条件2：包含触发关键词
        for kw in SPLIT_TRIGGER_KEYWORDS:
            if kw in text:
                return True
        return False

    def _select_strategy(self, task: CommandTask) -> SplitStrategy:
        """根据任务内容自动选择最佳分段策略。"""
        import re

        text = task.raw_command
        # 检测章节标记
        if re.search(r"第[一二三四五六七八九十百千\d]+章|Chapter\s+\d+", text, re.IGNORECASE):
            return self._strategies["by_chapter"]
        # 默认使用按字数拆分
        return self._strategies["by_length"]

    def split(self, task: CommandTask) -> list[Segment]:
        """
        对 CommandTask 执行智能分段。

        补丁：入口优先读取 segment_strategy 显式控制值：
          - force_split → 跳过字符/关键词判断，直接拆分（短指令逻辑分片 2-3 段）；
          - no_split    → 永远只生成 1 个 Segment；
          - auto        → 完整保留原有判断逻辑（字符阈值 + 关键词），完全不变。
        """
        strategy_name = normalize_segment_strategy(task.segment_strategy)
        logger.info(
            "[SPLIT_STRATEGY:%s] 任务 %s 进入拆分决策（指令长度=%d）",
            strategy_name, task.task_id, len(task.raw_command),
        )

        # ══════════ 补丁新增分支 ══════════
        if strategy_name == SEGMENT_STRATEGY_NO_SPLIT:
            logger.info(
                "[SPLIT_STRATEGY:no_split] 任务 %s 强制不拆分，产出单一分段",
                task.task_id,
            )
            return [self._build_single_segment(task)]

        if strategy_name == SEGMENT_STRATEGY_FORCE_SPLIT:
            if len(task.raw_command) >= DEFAULT_CHAR_THRESHOLD:
                # 长文本：走既有拆分算法，保持细分粒度
                strategy = self._select_strategy(task)
                segments = strategy.split(task)
                logger.info(
                    "[SPLIT_STRATEGY:force_split] 任务 %s 长文本强制拆分，"
                    "算法=%s，产出 %d 个分段",
                    task.task_id, strategy.name, len(segments),
                )
                return segments
            # 短指令高负载场景：基于任务元信息逻辑分片，产出 2-3 段
            segments = self._logical_shard_segments(task)
            logger.info(
                "[SPLIT_STRATEGY:force_split] 任务 %s 短指令逻辑分片，"
                "产出 %d 个分段（跳过字符/关键词判断）",
                task.task_id, len(segments),
            )
            return segments

        # ══════════ 以下为原有逻辑（auto / 注册策略），100% 保留 ══════════

        # 如果指定了具体策略
        if strategy_name != "auto" and strategy_name in self._strategies:
            logger.info("使用指定策略 '%s' 拆分任务 %s", strategy_name, task.task_id)
            return self._strategies[strategy_name].split(task)

        # auto 模式：检测是否需要分段
        if not self._needs_splitting(task):
            # 不需要分段，整体作为单个 Segment
            logger.info("任务 %s 无需分段，作为单一分段处理", task.task_id)
            return [self._build_single_segment(task)]

        # ── 专项2修复：token 预算约束切割 ──
        # 原 auto 逻辑仅按字符/关键词分段，无全局 token 预算。
        # 此处复用 load_estimator 的轻量估算（不引入额外依赖）：
        # 当指令估算 token 超过预算时，按预算可容纳的目标字符数
        # 自适应缩小 chunk_size，确保单段不溢出模型上下文。
        try:
            from core.config_manager import config_manager
            from services.load_estimator import LoadEstimator

            _le = LoadEstimator()
            _budget = int(config_manager.get("load_estimator.token_budget", 8192))
            _over, _est, _ = _le.check_token_budget(task.raw_command)
            if _over and _est > 0:
                # 目标字符数 = 预算 token × 每 token 字符数（中文按 1.5 字/token 折算）
                _target_chars = max(CHAR_BUDGET_MIN_CHUNK, int(_budget * 1.2))
                _chunk_limited = max(CHAR_BUDGET_MIN_CHUNK, min(DEFAULT_CHUNK_SIZE, _target_chars))
                # 预算约束下按更小粒度切割（动态 chunk_size）
                _limited = ByLengthStrategy(chunk_size=_chunk_limited)
                logger.warning(
                    "[SPLIT_STRATEGY:auto][预算约束] 任务 %s 估算 token=%d 超预算 %d，"
                    "按预算约束切割 chunk_size=%d（原 %d）",
                    task.task_id, _est, _budget, _chunk_limited, DEFAULT_CHUNK_SIZE,
                )
                self._strategies["by_length_limited"] = _limited
                return _limited.split(task)
        except Exception as exc:
            logger.warning("[SPLIT_STRATEGY:auto] 预算约束切割异常，退回首段后原始逻辑: %s", exc)

        # 自动选择最佳策略
        strategy = self._select_strategy(task)
        logger.info("自动选择策略 '%s' 拆分任务 %s", strategy.name, task.task_id)
        return strategy.split(task)

    # ── 补丁辅助：单段构造与逻辑分片 ─────────────────────────────

    @staticmethod
    def _build_single_segment(task: CommandTask) -> Segment:
        """构造单一分段（与原有 auto 单段逻辑完全一致）。"""
        return Segment(
            segment_id=f"seg_{uuid.uuid4().hex[:12]}",
            parent_task_id=task.task_id,
            content_payload=task.raw_command,
            sequence_order=0,
            tail_context={},
        )

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """按常见句读符拆句（补丁逻辑分片辅助）。"""
        import re

        parts = re.split(r"[。！？!?\n；;]+", text)
        return [p.strip() for p in parts if p.strip()]

    def _logical_shard_segments(self, task: CommandTask) -> list[Segment]:
        """
        补丁：短指令高负载任务的逻辑分片。

        不依赖 raw_command 文本长度，产出有序的 2-3 个 Segment，
        全部初始化空 tail_context（尾巴接力从第一段开始累积）：
          1. 可自然拆句 → 按句分片（超出上限时合并余量，控制 2-3 段）；
          2. 无句读符但长度可切 → 固定尺寸三等分；
          3. 极短指令 → 生成 3 个同载荷逻辑分片（重负载钩子按序装载外部数据，
             如书籍/文档内容，每个分片即一个处理节点与断点恢复锚点）。
        """
        text = task.raw_command
        parts = self._split_sentences(text)

        if len(parts) >= 2:
            if len(parts) > FORCE_SPLIT_MAX_CHUNKS:
                # 控制产出上限：前 2 句独立成段，其余合并为末段
                parts = parts[: FORCE_SPLIT_MAX_CHUNKS - 1] + [
                    " ".join(parts[FORCE_SPLIT_MAX_CHUNKS - 1:])
                ]
            chunks = parts
        elif len(text) >= FORCE_SPLIT_MIN_MEANINGFUL_LEN:
            # 无自然断句：固定尺寸切分（默认 3 段）
            n = FORCE_SPLIT_MAX_CHUNKS
            size = max(1, math.ceil(len(text) / n))
            chunks = [text[i:i + size] for i in range(0, len(text), size)][:n]
        else:
            # 极短指令：逻辑分片（每个分片携带完整指令，供重负载业务按序装载外部数据）
            logger.info(
                "[SPLIT_STRATEGY:force_split] 任务 %s 指令极短（%d字符），"
                "按逻辑节点生成 %d 个同载荷分片",
                task.task_id, len(text), FORCE_SPLIT_MAX_CHUNKS,
            )
            chunks = [text] * FORCE_SPLIT_MAX_CHUNKS

        return [
            Segment(
                segment_id=f"seg_{uuid.uuid4().hex[:12]}",
                parent_task_id=task.task_id,
                content_payload=chunk,
                sequence_order=order,
                tail_context={},
            )
            for order, chunk in enumerate(chunks)
        ]
