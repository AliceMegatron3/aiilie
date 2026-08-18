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
import hashlib
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
        # 提取策略组：默认组合策略（LLM 优先 + 规则兜底）
        self.strategy = strategy
        self._rule_strategy = None
        self._llm_strategy = None
        # 尝试拆出 rule / llm 子策略，供按任务切换提取模式使用
        from strategies.extraction import DefaultStrategy, FallbackStrategy
        from strategies.llm_extraction import LLMExtractionStrategy

        if isinstance(strategy, FallbackStrategy):
            self._llm_strategy = strategy.primary
            self._rule_strategy = strategy.fallback
        elif isinstance(strategy, LLMExtractionStrategy):
            self._llm_strategy = strategy
        elif isinstance(strategy, DefaultStrategy):
            self._rule_strategy = strategy

        # 书库原始文件所在目录
        self.books_dir = get_app_data_dir().parent / "library" / "books"
        self.books_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_strategy(self, extraction: str | None = None) -> Any:
        """按任务级 extraction 模式解析提取策略。

        - "llm":  仅 LLM 提取（无 LLM 策略时回退默认策略）
        - "rule": 仅规则提取
        - 其他(auto/None): 使用默认组合策略（LLM 优先 + 规则兜底）
        """
        mode = (extraction or "").strip().lower()
        if mode == "rule" and self._rule_strategy is not None:
            return self._rule_strategy
        if mode == "llm" and self._llm_strategy is not None:
            return self._llm_strategy
        return self.strategy
    async def submit_quantize_task(
        self,
        book_id: str,
        mode: str = "both",
        segment_strategy: str = "force_split",
        model: str | None = None,
        extraction: str | None = None,
        quantize_round: int = 1,
    ) -> str:
        """
        创建一个新的书籍量化任务。

        quantize_round: 1=粗扫 2=深挖 3=精炼
        """
        if extraction is None:
            from core.config_manager import config_manager
            extraction = config_manager.get("library.quantize_extraction", "auto") or "auto"
        model_suffix = f" model={model}" if model else ""
        task = QuantizeTask(
            book_id=book_id,
            raw_command=f"quantize book {book_id} mode={mode} round={quantize_round}{model_suffix}",
            priority=2,
            status="PENDING",
            segment_strategy=segment_strategy,
            **{"mode": mode, "model": model, "extraction": extraction, "quantize_round": quantize_round}
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

        # 获取量化轮次
        quantize_round = getattr(task, "quantize_round", 1)
        round_label = {1: "粗扫", 2: "深挖", 3: "精炼"}.get(quantize_round, f"R{quantize_round}")

        # WebSocket 启动通知
        try:
            from api.websocket import manager
            await manager.broadcast({
                "type": "quantize_start",
                "task_id": task_id,
                "book_id": book_id,
                "mode": getattr(task, "mode", "both"),
                "model": getattr(task, "model", None),
                "round": quantize_round,
                "round_label": round_label,
            })
        except Exception:
            pass

        # 1. 读取书籍原始内容（safe_join 防 book_id 路径遍历）。
        # 原文不存在时必须失败，不能生成没有来源的正式卡片。
        book_file = safe_join(self.books_dir, f"{book_id}.txt")
        if not book_file.exists() or not book_file.is_file():
            raise FileNotFoundError(f"书籍原文不存在，无法量化: {book_id}")
        raw_text = book_file.read_text(encoding="utf-8")
        if not raw_text.strip():
            raise ValueError(f"书籍原文为空，无法量化: {book_id}")
        source_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
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
        total_segs = len(segs_to_run := [s for s in segments if s["status"] != "COMPLETED"])
        seg_idx = 0
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
                    "tail_context": prev_tail,
                    "source_document_id": book_id,
                    "source_hash": source_hash,
                    "source_anchor": {
                        "source_document_id": book_id,
                        "source_path": str(book_file),
                        "paragraph": seg["sequence_order"],
                    },
                    "quantize_round": quantize_round,
                    "mode": getattr(task, "mode", "both"),
                    "model": getattr(task, "model", None),
                }
                
                # 核心联动：调用批次2 ExtractionStrategy 提取卡片。
                # round 1=粗扫，round 2=深挖，round 3=精炼；具体策略仍由任务模式决定。
                extraction_mode = getattr(task, "extraction", None)
                if quantize_round == 2:
                    extraction_mode = "llm"
                elif quantize_round == 3:
                    extraction_mode = "rule"
                strategy = self._resolve_strategy(extraction_mode)
                cards = await strategy.extract(seg["content_payload"], context_payload)

                # 只保留真实提取结果。摘要必须由提取策略产生，不能用固定模板伪造。
                # 精炼轮次按稳定指纹去重；其余轮次也通过索引 upsert 保证重试幂等。
                cards = self._filter_cards_for_task(
                    cards,
                    book_id,
                    seg,
                    source_hash,
                    mode=str(getattr(task, "mode", "both") or "both"),
                )
                if cards:
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

                # WebSocket 进度推送
                await self._broadcast_progress(task_id, book_id, seg_idx + 1, total_segs, len(cards))

                # 批量打分：新卡片自动计算 utility/entropy
                try:
                    await self.indexer.batch_score_cards(book_id)
                except Exception:
                    pass

                # 分类摘要卡检查（每 50 张触发）
                try:
                    await self._ensure_category_summary(book_id)
                except Exception:
                    pass

                # 避免极高并发导致过度占用资源
                seg_idx += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.exception("处理分段 %s 时发生错误", seg_id)
                await self.db.update_segment_status(seg_id, "FAILED", error_message=str(e))
                # 发生严重错误后中断整个大任务，等待后续调度重试
                raise RuntimeError(f"流水线分段失败，中断任务 {task_id}") from e
        logger.info("[Quantifier] 任务 %s 旗下全部分段已量化完成", task_id)

        # WebSocket 完成通知
        await self._broadcast_done(task_id, book_id)

        # 量化完成 → 触发量化反思（异步，不阻断主流程）
        try:
            await self._trigger_quantize_reflection(task_id, book_id, getattr(task, "mode", "both"))
        except Exception as e:
            logger.warning("[Quantifier] 量化反思触发失败（不影响主流程）: %s", e)

    @staticmethod
    def _filter_cards_for_task(
        cards: list[Any],
        book_id: str,
        segment: dict[str, Any],
        source_hash: str,
        mode: str = "both",
    ) -> list[Any]:
        """为量化产物补稳定来源指纹、按模式过滤并去重。"""
        seen: set[str] = set()
        filtered: list[Any] = []
        sequence = int(segment.get("sequence_order", 0))
        for card in cards or []:
            try:
                payload = card.model_dump(mode="json")
                content = str(payload.get("content") or "").strip()
                if not content:
                    continue
                card_type = str(payload.get("card_type", "info"))
                if mode == "info" and card_type != "info":
                    continue
                if mode == "data" and card_type != "data":
                    continue
                subtype = str(
                    payload.get("card_sub_type")
                    or payload.get("metric_type")
                    or payload.get("extension_module")
                    or ""
                )
                fingerprint = hashlib.sha256(
                    "|".join((source_hash, str(sequence), card_type, subtype, content)).encode("utf-8")
                ).hexdigest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                payload["card_id"] = f"card_{fingerprint[:24]}"
                payload["source_book_id"] = book_id
                payload["source_document_id"] = book_id
                payload["source_chapter"] = payload.get("source_chapter") or f"Part-{sequence}"
                payload["source_anchor"] = {
                    **(payload.get("source_anchor") or {}),
                    "source_document_id": book_id,
                    "paragraph": sequence,
                    "quote": payload.get("original_fragment", "") or content[:200],
                }
                filtered.append(card.__class__.model_validate(payload))
            except Exception as exc:
                logger.warning("跳过无法建立来源指纹的卡片: %s", exc)
        return filtered

    async def _broadcast_progress(self, task_id: str, book_id: str, completed: int, total: int, cards_in_seg: int) -> None:
        """向 AI 对话窗口推送量化进度。"""
        try:
            from api.websocket import manager
            await manager.broadcast({
                "type": "quantize_progress",
                "task_id": task_id,
                "book_id": book_id,
                "completed": completed,
                "total": total,
                "cards_in_seg": cards_in_seg,
            })
        except Exception:
            pass  # WebSocket 不可用时静默

    async def _broadcast_done(self, task_id: str, book_id: str) -> None:
        """向 AI 对话窗口推送量化完成通知。"""
        try:
            from api.websocket import manager
            await manager.broadcast({
                "type": "quantize_done",
                "task_id": task_id,
                "book_id": book_id,
            })
        except Exception:
            pass

    async def _trigger_quantize_reflection(self, task_id: str, book_id: str, mode: str) -> None:
        """量化完成后触发反思学习。"""
        # 统计本轮提取的卡片
        cards = await self.indexer.search_cards(source_book=book_id, limit=500)
        info_count = sum(1 for c in cards if c.get("card_type") == "info")
        data_count = sum(1 for c in cards if c.get("card_type") == "data")
        # 写入经验库
        from services.experience_manager import experience_manager
        content = f"量化书籍{book_id}完成，模式={mode}，共{len(cards)}张卡片（资料{info_count}/数据{data_count}）"
        experience_manager.add_experience("quantize", content)

        # P1：量化完成 → 唯一 ReflectionSession（幂等、provenance、可回放）。
        # 生成稳定来源指纹（book+mode），同输入重复量化不产生第二个会话/副作用。
        from services.quantify_reflection import create_quantify_reflection
        source_hash = hashlib.sha256(f"{book_id}|{mode}".encode("utf-8")).hexdigest()
        try:
            await create_quantify_reflection(
                self.indexer,
                book_id=book_id,
                source_hash=source_hash,
                mode=mode,
                metrics={"cards_total": len(cards), "info_count": info_count, "data_count": data_count},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Quantifier] 量化反思会话创建失败（不影响主流程）: %s", exc)

    async def _ensure_category_summary(self, book_id: str) -> None:
        """为书籍的主要分类生成/更新摘要卡（每 50 张卡片触发）。"""
        # 对资料卡的主要分类检查
        for category in ("worldview", "plot", "character", "style"):
            summary = await self.indexer.get_category_summary("info", category=category)
            if summary["count"] > 0 and summary["count"] % 50 == 0:
                # 生成/更新分类摘要卡
                from models.cards import InfoCard
                top_tags = ", ".join(t["tag"] for t in summary["top_tags"][:5])
                summary_card = InfoCard(
                    source_book_id=book_id,
                    content=f"【{category}分类摘要】共{summary['count']}张卡片，平均有用度{summary['avg_utility']}，"
                            f"高频标签: {top_tags or '无'}",
                    card_sub_type=f"auto_summary_{category}",
                    entropy_score=0.2,
                    utility_score=0.9,
                )
                summary_card.tags = ["auto_summary", f"category_{category}"]
                await self.indexer.save_card(summary_card)
                logger.info("[Quantifier] 已更新 %s 分类摘要卡（%d 张卡片）", category, summary["count"])