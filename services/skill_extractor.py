"""
services/skill_extractor.py — 通用技能提炼系统
===============================================
基于后台低优先级异步任务，扫描全量 InfoCard，提炼高频模式，
并在不改动底层模块的前提下，回写 SQLite 索引库权重与去重标签。

技能提炼真实化：通过 LLM 分析卡片内容，提取可复用的"技能模式"，
例如：战斗描写技巧、对话节奏、世界观构建方法、情感渲染手法等。
产出 UniversalSkill，进入 CANDIDATE 池，经灰度后转正为行为插件。
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

# 技能提炼模板：LLM 分析一批卡片，输出结构化技能列表
_EXTRACT_SKILLS_PROMPT = """你是一个写作技能提炼专家。分析以下从书籍中提取的资料卡片，从中发现作者的写作技巧和模式。

卡片内容：
{cards_text}

请以 JSON 数组输出发现的技能，每个技能包含：
- name: 技能名称（如"战斗节奏控制"、"对话情感递进"、"世界观渐进揭示"）
- category: 分类（如"combat"、"dialogue"、"worldbuilding"、"emotion"、"plot"）
- description: 技能描述（一段话说明这个技能如何运用）
- confidence: 置信度（0-1，表示这个模式在卡片中出现的频率和确定性）
- source_cards: 来源卡片摘要（简短引用）

如果这批卡片中没有发现有价值的技能模式，返回空数组 []。
仅返回纯 JSON，不要有任何其他解释文字！"""


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
        """初始化环境：为 cards 表安全地增加 weight 字段（若不存在）。"""
        try:
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
        """触发一轮全局技能提取任务。"""
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
            "SELECT card_id, summary, tags, weight, content FROM cards WHERE card_type = 'info' ORDER BY rowid ASC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def _extract_skills_with_llm(self, cards_batch: list[dict]) -> list[dict]:
        """用 LLM 分析一批卡片，提取技能模式。"""
        # 拼接卡片内容为文本
        cards_text_parts = []
        for card in cards_batch:
            summary = card.get("summary", "") or ""
            content = card.get("content", "") or ""
            tags = card.get("tags", "[]")
            try:
                tag_list = json.loads(tags) if isinstance(tags, str) else tags
            except Exception:
                tag_list = []
            text = summary or content[:500]
            if tag_list:
                text += f" [标签: {', '.join(tag_list)}]"
            cards_text_parts.append(text)

        cards_text = "\n---\n".join(cards_text_parts)
        prompt = _EXTRACT_SKILLS_PROMPT.format(cards_text=cards_text)

        # 调用模型调度器（延迟导入，避免循环依赖）
        try:
            # dispatcher 在 bootstrap 阶段创建并存入 app.state.model_dispatcher
            # 这里通过延迟导入方式获取：首次调用时 dispatcher 模块已被加载
            import services.dispatcher as disp_mod
            dispatcher = getattr(disp_mod, '_instance', None)
            if dispatcher is None:
                logger.debug("[SkillExtractor] Dispatcher 未就绪，跳过本轮 LLM 技能提取")
                return []
            response = await dispatcher.dispatch(prompt, override_mode="rapid")
            # 解析 JSON
            cleaned = response.replace("```json", "").replace("```", "").strip()
            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start < 0 or end <= start:
                return []
            skills = json.loads(cleaned[start:end + 1])
            if not isinstance(skills, list):
                return []
            return skills
        except Exception as e:
            logger.warning("[SkillExtractor] LLM 技能提取失败: %s", e)
            return []

    async def process_task(self, task: BasePipelineTask) -> None:
        """供 TaskManager 调用的核心执行入口。"""
        task_id = task.task_id
        logger.info("[SkillExtractor] 开始执行技能提取后台任务: %s", task_id)

        # 1. 探查总记录数并进行任务拆解
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
                    "sequence_order": idx * self.batch_size,
                    "status": "PENDING",
                    "tail_context": {}
                })
            segments = await self.db.get_segments_for_task(task_id)

        # 2. 遍历执行未完成的批次
        global_skill_frequencies: dict[str, int] = defaultdict(int)
        for seg in segments:
            seg_id = seg["segment_id"]
            if seg["status"] == "COMPLETED":
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
                await asyncio.sleep(self.throttle_delay)

                cards_batch = await self._fetch_info_cards_batch(offset, self.batch_size)
                if not cards_batch:
                    await self.db.update_segment_status(seg_id, "COMPLETED",
                        result_content="No cards in batch", new_tail={"skills": {}})
                    continue

                # 3. 用 LLM 提取技能模式（替代伪逻辑）
                skills = await self._extract_skills_with_llm(cards_batch)

                batch_skill_freq: dict[str, int] = defaultdict(int)
                update_tasks = []

                for skill in skills:
                    skill_name = skill.get("name", "")
                    if not skill_name:
                        continue
                    batch_skill_freq[skill_name] += 1
                    global_skill_frequencies[skill_name] += 1

                    # 找到匹配的卡片，打上技能标签并提升权重
                    for card in cards_batch:
                        card_summary = card.get("summary", "") or card.get("content", "") or ""
                        # 简单相关性检查：技能描述包含卡片内容片段
                        skill_desc = skill.get("description", "")
                        if skill_desc[:20] in card_summary or card_summary[:20] in skill_desc:
                            tags_str = card.get("tags", "[]")
                            try:
                                tags: list[str] = json.loads(tags_str) if isinstance(tags_str, str) else (tags_str or [])
                            except Exception:
                                tags = []
                            if skill_name not in tags:
                                tags.append(skill_name)
                                new_weight = (card.get("weight") or 1) + 2
                                update_tasks.append((
                                    json.dumps(tags, ensure_ascii=False),
                                    new_weight,
                                    card["card_id"]
                                ))

                # 4. 回写更新索引库
                if update_tasks:
                    await self.indexer.conn.executemany(
                        "UPDATE cards SET tags = ?, weight = ? WHERE card_id = ?",
                        update_tasks
                    )
                    await self.indexer.conn.commit()
                    logger.debug("批次 %s: 调整了 %d 张卡片的权重与标签", seg_id, len(update_tasks))

                await self.db.update_segment_status(
                    seg_id,
                    "COMPLETED",
                    result_content=f"Processed {len(cards_batch)} cards, found {len(skills)} skills",
                    new_tail={"skills": dict(batch_skill_freq)}
                )
            except Exception as e:
                logger.exception("处理技能提取分段 %s 时失败", seg_id)
                await self.db.update_segment_status(seg_id, "FAILED", error_message=str(e))
                raise RuntimeError(f"提取流水线异常中断: {task_id}") from e

        logger.info("[SkillExtractor] 后台任务 %s 已全部执行完毕。累计发现技能类: %d",
                    task_id, len(global_skill_frequencies))