"""
services/emotion_engine/quantifier_service.py — 九阶情感量化服务（补丁G完整落地）
===============================================================================
- quantify_single_fragment：单片段实时量化，接入真实 LLM（不再返回 [MOCK] 占位）；
- start_batch_quantize_task：整本书批量量化封装批次1 CommandTask
  （CommandSplitter 分片 + Segment 断点续算由批次1引擎负责）；
- 量化结果可选导出为批次2 DataCard（metric_type="emotion_frame"）。
"""
import json
import logging
import re
from typing import Any, List, Optional

from models.emotion_models import EmotionFrame, build_frame_from_payload
from services.prompt_template_manager import prompt_manager
from services.emotion_engine.frame_manager import EmotionFrameManager

logger = logging.getLogger(__name__)


class EmotionQuantifier:
    def __init__(
        self,
        frame_manager: EmotionFrameManager,
        batch1_task_manager=None,
        indexer=None,
    ) -> None:
        self.frame_manager = frame_manager
        # 批次1 分段执行引擎（批量量化任务投递；None 时仅支持单片段模式）
        self.batch1_task_manager = batch1_task_manager
        # 批次2 卡片索引器（emotion_frame 指标卡导出；可选）
        self.indexer = indexer

    @staticmethod
    def _parse_llm_payload(raw: str) -> dict:
        """容错解析 LLM 返回的情感 JSON（剥离 markdown 代码块/多余文本）。"""
        if not raw:
            raise ValueError("LLM 返回为空")
        cleaned = re.sub(r"```(json)?", "", raw).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"LLM 返回非 JSON: {raw[:120]}")
        return json.loads(cleaned[start:end + 1])

    @staticmethod
    def _normalize_payload(data: dict, text: str, project_id: str | None,
                           book_id: str | None) -> dict:
        """把 LLM 输出规整为帧 payload（三路情绪 + wave_level + mode）。"""
        wave = data.get("wave_level")
        try:
            wave = int(wave)
        except (TypeError, ValueError):
            wave = 5
        wave = max(1, min(9, wave))
        mode = str(data.get("mode", "mixed")).lower()
        if mode not in ("single", "mixed", "chaotic"):
            mode = "mixed"

        def _as_dict(v: Any) -> dict:
            return v if isinstance(v, dict) else {}

        return {
            "frame_type": data.get("frame_type", "standard"),
            "source_project_id": project_id,
            "source_book_id": book_id,
            "source_chapter": data.get("source_chapter"),
            "text_snippet": (text or "")[:200],
            "sequence": int(data.get("sequence", 0) or 0),
            "scene_emotion": _as_dict(data.get("scene_emotion")),
            "character_inner_emotion": _as_dict(data.get("character_inner_emotion")),
            "conflict_emotion": _as_dict(data.get("conflict_emotion")),
            "wave_level": wave,
            "mode": mode,
        }

    async def quantify_single_fragment(
        self,
        text: str,
        project_id: str = None,
        book_id: str = None,
        llm_client=None,
    ) -> EmotionFrame:
        """
        模式 A：单片段实时量化（真实 LLM，无 [MOCK]）。
        llm_client 未提供或云端未启用时抛 CloudServiceDisabledError（上层转 503）。
        """
        # 1. 渲染 Prompt（补丁F模板系统）
        prompt = prompt_manager.render("emotion_quantify_single", {"text": text})

        # 2. 调用真实 LLM
        if llm_client is None:
            from core.exceptions import CloudServiceDisabledError

            raise CloudServiceDisabledError("情感量化云端大模型")
        raw = await llm_client.generate_completion(prompt, temperature=0.3, max_tokens=1024)

        # 3. 解析 + 规整 + 持久化帧
        data = self._parse_llm_payload(raw)
        payload = self._normalize_payload(data, text, project_id, book_id)
        frame = await self.frame_manager.create_frame(payload)

        # 4. 可选：导出批次2 DataCard（metric_type="emotion_frame"）
        if self.indexer is not None and book_id:
            await self._export_emotion_card(frame)
        return frame

    async def _export_emotion_card(self, frame: EmotionFrame) -> None:
        """把情感帧导出为 DataCard（metric_type=emotion_frame）。"""
        try:
            from models.cards import DataCard

            card = DataCard(
                source_book_id=frame.source_book_id or "",
                source_chapter=frame.source_chapter or "",
                content=json.dumps(frame.model_dump(), ensure_ascii=False, default=str),
                metric_type="emotion_frame",
                value={
                    "frame_id": frame.frame_id,
                    "wave_level": frame.wave_level,
                    "mode": frame.mode,
                },
                tags=["emotion_frame", f"wave_{frame.wave_level}"],
            )
            await self.indexer.save_card(card)
            logger.info("[EmotionQuantifier] 情感帧已导出 DataCard: %s", frame.frame_id)
        except Exception as exc:
            logger.warning("[EmotionQuantifier] 情感卡片导出失败（不阻断量化）: %s", exc)

    async def start_batch_quantize_task(
        self, book_id: str, split_segments: List[str] = None
    ) -> str:
        """
        模式 B：整本书批量情感量化 → 封装批次1 CommandTask。

        分片与断点续算全部由批次1引擎负责：
        - CommandSplitter 按 load_estimator/章节切分；
        - 任务中断重启后已 COMPLETED 的 Segment 自动跳过（断点续算）。
        返回批次1任务 task_id。
        """
        if self.batch1_task_manager is None:
            raise RuntimeError("批次1 分段执行引擎未注入，无法提交批量量化任务")

        task = await self.batch1_task_manager.submit_task(
            raw_command=f"emotion quantize book {book_id}",
            priority=5,  # 低优先级后台任务，不阻塞 HTTP
            segment_strategy="force_split",
        )
        logger.info(
            "[EmotionQuantifier] 已提交书籍情感批量量化任务: %s (book=%s)",
            task.task_id, book_id,
        )
        return task.task_id

    def render_generation_prompt(self, frame: EmotionFrame, context_frames: List[EmotionFrame]) -> str:
        """
        根据模式渲染具体生成小说文本的 Prompt（补丁F模板系统）。
        7-9 混沌模式走专属模板 emotion_chaotic_render。
        """
        variables = {
            "current_frame": frame.model_dump(),
            "context_frames": [f.model_dump() for f in context_frames],
        }

        if frame.mode == "chaotic" or frame.wave_level >= 7:
            # 7-9 混沌模式专属渲染模板
            return prompt_manager.render("emotion_chaotic_render", variables)
        return prompt_manager.render("emotion_generate_render_single", variables)
