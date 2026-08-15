"""
services/quantifier.py — 书籍量化流水线
=========================================
实现 BookQuantifier 调度类，串联批次1的执行引擎能力（任务分割、断点恢复）
与批次2的卡片生成系统（策略提取、注册中心、二分索引）。

P1-1.4 修复：移除本模块内的 TailContextManager 简化内存桩，
统一使用 services/tail_context_manager.TailContextManager（双模式落盘），
尾巴读写与批次1引擎同一实现；分段策略尊重 task.segment_strategy。
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from models.task import QuantizeTask
from typing import Any
from core.database import DatabaseManager
from core.path_resolver import get_app_data_dir, safe_join
from core.task_manager import TaskManager
from services.card_registry import CardTypeRegistry
from services.command_splitter import CommandSplitter
from services.indexer import CardIndexer
from services.tail_context_manager import TailContextManager
from strategies.extraction import ExtractionStrategy
logger = logging.getLogger(__name__)


class BookQuantifier:
    """
    书籍量化流水线调度器。
    基于 TaskManager 消费主任务，通过 CommandSplitter 拆解为 Segment，
    通过 ExtractionStrategy 提取，最终用 CardIndexer 落盘。
    """
    def __init__(
        self,
        db: DatabaseManager,
        task_manager: TaskManager,
        indexer: CardIndexer,
        registry: CardTypeRegistry,
        strategy: ExtractionStrategy,
        tail_manager: TailContextManager | None = None,
    ) -> None:
        self.db = db
        self.task_manager = task_manager
        self.indexer = indexer
        self.registry = registry
        self.strategy = strategy
        
        self.splitter = CommandSplitter()
        # P1-1.4：尾巴统一走真实 TailContextManager（双模式落盘/断点恢复）
        self.tail_manager = tail_manager or TailContextManager()
        
        # 书库原始文件所在目录
        self.books_dir = get_app_data_dir().parent / "library" / "books"
        self.books_dir.mkdir(parents=True, exist_ok=True)
    async def submit_quantize_task(
        self,
        book_id: str,
        mode: str = "both",
        segment_strategy: str = "force_split",
    ) -> str:
        """
        创建一个新的书籍量化任务，推入 TaskManager。
        返回生成的 task_id。

        B1-09：segment_strategy 即业务 hint_force（优先级高于 LoadEstimator
        自动预估），默认 force_split 保证量化重负载任务一定多节点分段；
        调用方可按需覆盖（如 no_split/auto 调试）。
        """
        task = QuantizeTask(
            book_id=book_id,
            raw_command=f"quantize book {book_id} mode={mode}",
            priority=2,
            status="PENDING",
            segment_strategy=segment_strategy,
            # mode can be added via extra kwargs since we have extra="allow"
            **{"mode": mode}
        )
        
        await self.task_manager.submit_task(task)
        logger.info("已创建量化任务: %s (Book: %s)", task.task_id, book_id)
        return task.task_id
    async def process_task(self, task: QuantizeTask) -> None:
        """
        注册到 TaskManager 的核心回调处理函数。
        执行主任务分解，并逐一处理各个 Segment 以支持断点续传。
        """
        task_id = task.task_id
        book_id = getattr(task, "book_id", None)
        
        if not book_id:
            # 尝试从 raw_command 中解析兼容
            cmd: str = getattr(task, "raw_command", "")
            if "quantize book" in cmd:
                parts = cmd.split()
                book_id = parts[2] if len(parts) > 2 else "unknown"
            else:
                raise ValueError("未知的书籍目标，无法执行量化任务。")
        logger.info("[Quantifier] 开始执行主量化任务: %s，目标书籍: %s", task_id, book_id)
        # 1. 尝试读取书籍原始内容（safe_join 防 book_id 路径遍历）
        book_file = safe_join(self.books_dir, f"{book_id}.txt")
        if not book_file.exists():
            # 模拟存在，避免实际运行时报错
            raw_text = f"这是书籍 {book_id} 的虚拟内容。在实际流程中应该有10万字小说..."
            logger.warning("未找到实体书文件 %s，使用占位文本...", book_file)
        else:
            raw_text = book_file.read_text(encoding="utf-8")
        # 2. 从数据库查询是否已有生成的 Segment (用于断点恢复)
        segments = await self.db.get_segments_for_task(task_id)
        
        # 如果是首次运行（无 segments），调用批次1 CommandSplitter 进行语义拆解
        if not segments:
            logger.info("[Quantifier] 首次执行，开始调用 CommandSplitter 进行拆解...")
            from models.task import CommandTask

            # P1-1.4：尊重任务级分段策略（默认 force_split 保证量化重负载多节点分段）
            strategy = getattr(task, "segment_strategy", None) or "force_split"
            split_task = CommandTask(
                task_id=f"quant_split_{book_id}",
                raw_command=raw_text,
                segment_strategy=strategy,
            )
            chunks = [s.content_payload for s in self.splitter.split(split_task)]
            logger.info(
                "书籍原文长度 %d，语义拆分为 %d 个处理分段", len(raw_text), len(chunks)
            )

            for idx, chunk in enumerate(chunks):
                seg_id = f"seg_{task_id}_{idx}"
                seg_data = {
                    "segment_id": seg_id,
                    "parent_task_id": task_id,
                    "content_payload": chunk,
                    "sequence_order": idx,
                    "status": "PENDING",
                    "tail_context": {}
                }
                await self.db.insert_segment(seg_data)
            
            # 重新获取插入后的 segments
            segments = await self.db.get_segments_for_task(task_id)
        # 3. 遍历执行每一个尚未完成的 Segment
        for seg in segments:
            seg_id = seg["segment_id"]
            if seg["status"] == "COMPLETED":
                logger.debug("跳过已完成的分段: %s", seg_id)
                continue
                
            try:
                # 标记该分段为运行中
                await self.db.update_segment_status(seg_id, "RUNNING")
                
                # 读取上文环境，保持语义连续性
                # 优先使用分段库中持久化的 tail_context（断点恢复语义），
                # P1-1.4：统一经 TailContextManager 物化（支持大尾巴落盘还原），
                # 替代原内存 dict 简化桩。
                try:
                    seg_tail = seg.get("tail_context")
                    if isinstance(seg_tail, str):
                        prev_tail = json.loads(seg_tail or "{}")
                    else:
                        prev_tail = seg_tail or {}
                    if not isinstance(prev_tail, dict):
                        raise TypeError("tail_context 非 dict")
                    prev_tail = self.tail_manager.materialize(prev_tail)
                except (json.JSONDecodeError, TypeError):
                    prev_tail = {}
                context_payload = {
                    "book_id": book_id,
                    "chapter": f"Part-{seg['sequence_order']}",
                    "tail_context": prev_tail
                }
                
                # ==========================================
                # 核心联动：调用批次2 ExtractionStrategy 提取卡片
                # ==========================================
                logger.info("正在量化提取分段: %s", seg_id)
                cards = await self.strategy.extract(seg["content_payload"], context_payload)
                
                # ==========================================
                # 核心联动：调用批次2 CardIndexer 落盘存储
                # ==========================================
                
                # 【事件简报专员】节点逻辑：在量化结束前自动生成 SummaryCard
                from models.cards import InfoCard
                summary_content = f"本段内容量化摘要，共提取了 {len(cards)} 张细节卡片。总结该分段剧情进展与核心事件..."
                summary_card = InfoCard(
                    source_book_id=book_id,
                    content=summary_content,
                    card_sub_type="summary",
                    entropy_score=0.3,
                    utility_score=0.8
                )
                summary_card.tags = ["auto_summary", f"part_{seg['sequence_order']}"]
                
                # 统一批量写入
                cards.append(summary_card)
                await self.indexer.save_cards(cards)
                
                # 模拟提取新的句尾上下文并更新
                # P1-1.4：尾巴经真实 TailContextManager 落盘/驻留（超大时磁盘卸载）
                new_tail = {
                    "last_extracted_cards": len(cards),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **prev_tail,
                }
                stored_tail = self.tail_manager.store(task_id, seg_id, new_tail)
                
                # 记录最终状态
                await self.db.update_segment_status(
                    seg_id, 
                    "COMPLETED", 
                    result_content=f"Generated {len(cards)} cards", 
                    new_tail=stored_tail
                )
                
                # 避免极高并发导致过度占用资源
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.exception("处理分段 %s 时发生错误", seg_id)
                await self.db.update_segment_status(seg_id, "FAILED", error_message=str(e))
                # 发生严重错误后中断整个大任务，等待后续调度重试
                raise RuntimeError(f"流水线分段失败，中断任务 {task_id}") from e
        logger.info("[Quantifier] 任务 %s 旗下全部分段已量化完成", task_id)