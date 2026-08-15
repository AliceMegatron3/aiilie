"""
services/skill_extractor.py — 通用技能提炼系统
===============================================
基于后台低优先级异步任务，扫描全量 InfoCard，提炼高频模式，
并在不改动底层模块的前提下，回写 SQLite 索引库权重与去重标签。
"""
from __future__ import annotations
import asyncio
import json
import logging
import uuid
from models.task import BasePipelineTask
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from core.database import DatabaseManager
from core.task_manager import TaskManager
from services.indexer import CardIndexer
logger = logging.getLogger(__name__)
class UniversalSkillExtractor:
    """
    通用技能卡后台提取器。
    适配海量数据静默分析，内置频次限流、进度持久化与卡片权重动态调整。
    """
    def __init__(
        self,
        db: DatabaseManager,
        indexer: CardIndexer,
        task_manager: TaskManager,
        batch_size: int = 100,
        throttle_delay: float = 0.5
    ) -> None:
        self.db = db
        self.indexer = indexer
        self.task_manager = task_manager
        self.batch_size = batch_size
        self.throttle_delay = throttle_delay
    async def initialize(self) -> None:
        """
        初始化环境。
        无侵入式兼容：为 cards 表安全地增加 weight 字段（若不存在）。
        """
        try:
            # 使用 PRAGMA 检查列是否存在，避免 ALTER TABLE 冲突
            cursor = await self.indexer.conn.execute("PRAGMA table_info(cards)")
            rows = await cursor.fetchall()
            columns = [row[1] for row in rows]
            if "weight" not in columns:
                await self.indexer.conn.execute("ALTER TABLE cards ADD COLUMN weight INTEGER DEFAULT 1")
                await self.indexer.conn.commit()
                logger.info("已成功为卡片索引表追加检索权重(weight)字段。")
            else:
                logger.info("卡片索引表已存在 weight 字段，跳过。")
        except Exception as e:
            logger.warning("追加权重字段发生未知错误: %s", e)
    async def submit_extraction_task(self) -> str:
        """
        触发一轮全局技能提取任务，投递至 TaskManager。
        设置为极低优先级 (priority = 7)，避免抢占用户正常操作。
        """
        task_id = f"skillext_{uuid.uuid4().hex[:8]}"
        task = BasePipelineTask(
            task_type="extract",
            raw_command=f"extract_skills_from_info_cards",
            priority=7,
            status="PENDING"
        )
        await self.task_manager.submit_task(task)
        logger.info("已投递通用技能提取后台任务: %s (Priority: 7)", task.task_id)
        return task.task_id
    async def _fetch_info_cards_batch(self, offset: int, limit: int) -> list[dict[str, Any]]:
        """从 SQLite 中按批次拉取待分析的资料卡。"""
        cursor = await self.indexer.conn.execute(
            "SELECT card_id, summary, tags, weight FROM cards WHERE card_type = 'info' ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    async def process_task(self, task: BasePipelineTask) -> None:
        """
        供 TaskManager 调用的核心执行入口。
        基于 DatabaseManager 的 Segments 实现物理断点续传与批次跟踪。
        """
        task_id = task.task_id
        logger.info("[SkillExtractor] 开始执行技能提取后台任务: %s", task_id)
        # 1. 探查总记录数并进行任务拆解（如果尚未拆解）
        segments = await self.db.get_segments_for_task(task_id)
        if not segments:
            cursor = await self.indexer.conn.execute("SELECT COUNT(*) FROM cards WHERE card_type = 'info'")
            row = await cursor.fetchone()
            total_cards = row[0] if row else 0
            
            total_segments = (total_cards + self.batch_size - 1) // self.batch_size
            logger.info("总计 %d 张InfoCard，将拆分为 %d 个提取分段", total_cards, total_segments)
            for idx in range(total_segments):
                seg_id = f"seg_{task_id}_{idx}"
                await self.db.insert_segment({
                    "segment_id": seg_id,
                    "parent_task_id": task_id,
                    "content_payload": "info_batch",
                    "sequence_order": idx * self.batch_size,  # 复用 order 存储 offset
                    "status": "PENDING",
                    "tail_context": {}
                })
            segments = await self.db.get_segments_for_task(task_id)
        # 2. 遍历执行未完成的批次 (保障断点续传)
        global_skill_frequencies: dict[str, int] = defaultdict(int)
        for seg in segments:
            seg_id = seg["segment_id"]
            if seg["status"] == "COMPLETED":
                # 恢复之前统计好的状态，避免断点续跑丢失全局频次
                try:
                    tail = json.loads(seg.get("tail_context") or "{}")
                    for k, v in tail.get("skills", {}).items():
                        global_skill_frequencies[k] += v
                except Exception:
                    pass
                continue
            await self.db.update_segment_status(seg_id, "RUNNING")
            offset = seg["sequence_order"]
            
            try:
                # 限流节流，让出 CPU 给主业务
                await asyncio.sleep(self.throttle_delay)
                
                cards_batch = await self._fetch_info_cards_batch(offset, self.batch_size)
                batch_skill_freq: dict[str, int] = defaultdict(int)
                
                # 3. 模拟静默分析逻辑：技能去重与基础权重梯度
                update_tasks = []
                for card in cards_batch:
                    # 模拟根据摘要或标签发现潜在的"通用技能" (如高频设定、叙事套路)
                    # 此处仅做简易文本哈希和关键字提取，实际需对接提取模型
                    summary = card.get("summary", "")
                    potential_skill = f"skill_derived_{len(summary) % 5}"  # 伪逻辑
                    
                    # 频次累加
                    batch_skill_freq[potential_skill] += 1
                    global_skill_frequencies[potential_skill] += 1
                    
                    # 生成新标签，去重保护
                    tags_str = card.get("tags", "[]")
                    try:
                        tags: list[str] = json.loads(tags_str) if tags_str else []
                    except Exception:
                        tags = []
                        
                    if potential_skill not in tags:
                        tags.append(potential_skill)
                        # 权重梯度：具有通用技能的卡片，检索权重 +2
                        new_weight = card.get("weight", 1) + 2
                        
                        update_tasks.append((
                            json.dumps(tags, ensure_ascii=False),
                            new_weight,
                            card["card_id"]
                        ))
                # 4. 回写更新索引库 (基于事物批量提交)
                if update_tasks:
                    await self.indexer.conn.executemany(
                        "UPDATE cards SET tags = ?, weight = ? WHERE card_id = ?",
                        update_tasks
                    )
                    await self.indexer.conn.commit()
                    logger.debug("批次 %s: 调整了 %d 张卡片的权重与标签", seg_id, len(update_tasks))
                # 记录分段完成状态，并持久化本批次统计用于状态合并
                await self.db.update_segment_status(
                    seg_id, 
                    "COMPLETED",
                    result_content=f"Processed {len(cards_batch)} cards",
                    new_tail={"skills": dict(batch_skill_freq)}
                )
            except Exception as e:
                logger.exception("处理技能提取分段 %s 时失败", seg_id)
                await self.db.update_segment_status(seg_id, "FAILED", error_message=str(e))
                raise RuntimeError(f"提取流水线异常中断: {task_id}") from e
        logger.info("[SkillExtractor] 后台任务 %s 已全部执行完毕。累计发现潜在技能类: %d", 
                    task_id, len(global_skill_frequencies))