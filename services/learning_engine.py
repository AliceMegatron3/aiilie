"""
services/learning_engine.py — 文档自学习核心引擎
===================================================
通过 TaskManager 驱动，分段阅读 ProjectDoc 长文档并调用 ModelDispatcher。
解析完成后落地存储到 ai_parse.json，赋能批次3与批次4的检索问答体系。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any
from models.task import LearningTask

# 章节标记识别（与 services.command_splitter.ByChapterStrategy 保持同语义）
_CHAPTER_PATTERN = re.compile(
    r"(?=(?:第[一二三四五六七八九十百千\d]+章|Chapter\s+\d+))", re.IGNORECASE
)


def _chunk_by_length(text: str, max_length: int = 2000) -> list[str]:
    """按固定字符窗口切块（无章节标记时的兜底）。"""
    return [text[i:i + max_length] for i in range(0, len(text), max_length)] or [""]


def _split_document(text: str, max_length: int = 2000) -> list[str]:
    """文档切块：优先按章节标记切分，无章节标记时按固定字数窗口兜底。

    复用 command_splitter 的章节识别语义（第X章 / Chapter X），
    使文档学习分段粒度与小说章节结构对齐；章节内超长再二次切块，
    保证单段不超 LLM Context Window。
    """
    if not text:
        return [""]

    parts = [p.strip() for p in _CHAPTER_PATTERN.split(text) if p.strip()]
    if len(parts) >= 2:
        chunks: list[str] = []
        for part in parts:
            chunks.extend(_chunk_by_length(part, max_length))
        return chunks
    return _chunk_by_length(text, max_length)


from core.database import DatabaseManager
from core.task_manager import TaskManager
from models.project import AIParseResult
from services.dispatcher import ModelDispatcher
from services.project_manager import ProjectManager
from strategies.learning import DefaultLearningStrategy, LearningStrategy

logger = logging.getLogger(__name__)


class DocumentLearningEngine:
    """
    长时文档解析自学习引擎。
    包含断点续读、跨段落模型调度与结构化成果产出功能。
    """

    def __init__(
        self,
        db: DatabaseManager,
        task_manager: TaskManager,
        project_manager: ProjectManager,
        dispatcher: ModelDispatcher,
        default_strategy: LearningStrategy | None = None
    ) -> None:
        self.db = db
        self.task_manager = task_manager
        self.project_manager = project_manager
        self.dispatcher = dispatcher
        self.strategy = default_strategy or DefaultLearningStrategy()

    async def submit_learning_task(
        self,
        doc_id: str,
        segment_strategy: str = "force_split",
    ) -> str:
        """
        向后台低优先级队列提交长文档学习任务。
        返回任务 UUID 供前端进度轮询。

        B1-09：segment_strategy 即业务 hint_force（优先级高于 LoadEstimator
        自动预估），默认 force_split 保证文档学习重负载任务一定多节点分段；
        调用方可按需覆盖（如 no_split/auto 调试）。
        """
        doc = await self.project_manager.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")

        task = LearningTask(
            doc_id=doc_id,
            project_id=doc.project_id,
            priority=5,
            status="PENDING",
            segment_strategy=segment_strategy,
        )
        await self.task_manager.submit_task(task)
        logger.info("已提交文档学习长时后台任务: %s (Doc: %s)", task.task_id, doc_id)
        return task.task_id

    async def submit_feedback(self, feedback: dict) -> bool:
        """
        补丁2：角色 OOC 校对反馈通道。
        将反馈追加写入项目 ai_parse/ooc_feedback.jsonl，作为文档学习系统的
        额外学习样本（后续学习任务会读取该样本增强角色一致性）。
        """
        try:
            project_id = feedback.get("project_id") or ""
            doc_id = feedback.get("doc_id") or ""
            # 阶段B：project_id/doc_id 是用户可控字段，必须 safe_join 防路径遍历。
            from core.path_resolver import safe_join

            parse_dir = safe_join(self.project_manager.projects_dir, project_id, "ai_parse")
            parse_dir.mkdir(parents=True, exist_ok=True)
            feedback_file = safe_join(parse_dir, f"{doc_id}_ooc_feedback.jsonl")

            def _append_feedback() -> None:
                with open(feedback_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(feedback, ensure_ascii=False) + "\n")

            await asyncio.to_thread(_append_feedback)
            logger.info(
                "[LearningEngine] OOC 反馈已写入学习样本: project=%s doc=%s char=%s",
                project_id, doc_id, feedback.get("character"),
            )
            return True
        except Exception as exc:
            logger.warning("[LearningEngine] OOC 反馈写入失败: %s", exc)
            return False

    async def process_task(self, task: LearningTask) -> None:
        """
        由 TaskManager 后台消费触发的实际执行链路。
        1. 检查物理文档内容。
        2. 若未分段，调用 CommandSplitter 进行文本分段入库 (Segments)。
        3. 对 PENDING 段落发起多模式 AI 推理。
        4. 全部完成则聚合结果落地 ai_parse.json。
        """
        task_id = task.task_id
        doc_id = task.doc_id
        project_id = task.project_id

        logger.info("[LearningEngine] 开始执行文档自学习任务: %s", task_id)
        
        doc = await self.project_manager.get_document(doc_id)
        if not doc or not doc.raw_content:
            logger.warning("文档不存在或为空，无法执行学习: %s", doc_id)
            return

        # 1. 拆分与初始化 Segments (断点续读支持)
        segments = await self.db.get_segments_for_task(task_id)
        if not segments:
            # 调用文档切块策略避免超出大模型 Context Window
            text_chunks = _split_document(doc.raw_content, max_length=2000)
            logger.info("文档长度 %d，切割为 %d 个处理分段", len(doc.raw_content), len(text_chunks))
            
            for idx, chunk in enumerate(text_chunks):
                seg_id = f"seg_{task_id}_{idx}"
                await self.db.insert_segment({
                    "segment_id": seg_id,
                    "parent_task_id": task_id,
                    "content_payload": chunk,
                    "sequence_order": idx,
                    "status": "PENDING",
                    "tail_context": "{}"
                })
            segments = await self.db.get_segments_for_task(task_id)

        # 2. 遍历执行
        all_results = []
        is_fully_completed = True

        for seg in segments:
            seg_id = seg["segment_id"]
            if seg["status"] == "COMPLETED":
                # 加载旧进度
                try:
                    old_res = json.loads(seg.get("tail_context", "{}"))
                    if old_res:
                        all_results.append(old_res)
                except Exception:
                    pass
                continue

            await self.db.update_segment_status(seg_id, "RUNNING")
            chunk_text = seg["content_payload"]

            try:
                # 生成策略 Prompt
                prompt = self.strategy.generate_prompt(chunk_text)
                
                # 触发多模式智能调度：携带 project_id 以激活内部 bind_book_ids 上下文扩充
                # 分析任务强制采用 think 模式，进行深度推理挖掘
                response_text = await self.dispatcher.dispatch(
                    prompt=prompt,
                    project_id=project_id,
                    override_mode="think"
                )
                
                # 结构化解析
                parsed_res = self.strategy.parse_response(response_text)
                all_results.append(parsed_res)
                
                # 物理持久化断点状态，存入 tail_context
                await self.db.update_segment_status(
                    seg_id, 
                    "COMPLETED", 
                    result_content="Success", 
                    new_tail=parsed_res
                )
            except Exception as e:
                logger.error("学习分段 %s 执行失败: %s", seg_id, e)
                await self.db.update_segment_status(seg_id, "FAILED", error_message=str(e))
                is_fully_completed = False
                break # 中断，待后续重试

        # 3. 结果聚合与物理文件落盘
        if is_fully_completed and all_results:
            merged_dict = self.strategy.merge_results(all_results)
            merged_dict["doc_id"] = doc_id
            merged_dict["project_id"] = project_id
            
            final_result = AIParseResult(**merged_dict)
            
            # 定位目标落盘路径（阶段B：safe_join 防 project_id/doc_id 路径遍历）
            from core.path_resolver import safe_join

            parse_dir = safe_join(self.project_manager.projects_dir, project_id, "ai_parse")
            parse_dir.mkdir(parents=True, exist_ok=True)
            output_file = safe_join(parse_dir, f"{doc_id}_parse.json")
            
            output_file.write_text(final_result.model_dump_json(indent=2), encoding="utf-8")
            
            # 回写数据库，打通元数据检索层
            await self.project_manager.update_document_parse_result(doc_id, str(output_file))
            logger.info("[LearningEngine] 任务 %s 执行圆满成功，成果已写入: %s", task_id, output_file)
