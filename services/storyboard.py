"""
services/storyboard.py — 补丁4：场景可视化分镜生成系统
========================================================
StoryboardPromptBuilder : 提取场景描写 + 项目风格标签 → 构造生图 Prompt
StoryboardService       : 生图任务封装批次1 CommandTask(task_type=GENERATE_IMAGE)
                          图片保存至项目 assets/storyboard 目录
                          文本段落 ↔ 分镜图片锚点双向绑定（JSONL 索引）
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from core.config_manager import config_manager
from core.path_resolver import safe_join

logger = logging.getLogger(__name__)

# 场景描写特征词（用于段落提取）
_SCENE_HINTS = (
    "天空", "月光", "阳光", "雨", "雪", "风", "雾", "山", "海", "森林", "宫殿",
    "城", "街道", "房间", "战场", "废墟", "悬崖", "洞", "火", "光", "影", "夜色",
    "黄昏", "黎明", "清晨", "夕阳", "星辰", "云", "雷", "电", "血", "剑光",
)


def _feature_enabled() -> bool:
    return config_manager.get_bool("feature.storyboard_enable", False)


class StoryboardPromptBuilder:
    """场景描写提取 + 生图 Prompt 构造。"""

    def extract_scene_passages(
        self, text: str, max_passages: int = 8
    ) -> list[dict[str, Any]]:
        """从正文提取含场景描写的段落，返回 [{start, end, snippet, score}]。"""
        paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        passages: list[dict[str, Any]] = []
        offset = 0
        for para in paragraphs:
            start = text.index(para, offset)
            end = start + len(para)
            offset = end
            score = sum(1 for hint in _SCENE_HINTS if hint in para)
            if score >= 2 and len(para) >= 30:
                passages.append(
                    {
                        "start": start,
                        "end": end,
                        "snippet": para[:200],
                        "score": score,
                    }
                )
        passages.sort(key=lambda p: (-p["score"], p["start"]))
        return passages[:max_passages]

    def build_prompt(self, scene_text: str, style_tags: list[str] | None = None) -> str:
        """结合项目风格标签构造生图 Prompt（中文描述 + 风格后缀）。"""
        tags = style_tags or []
        style_suffix = "、".join(tags) if tags else "电影级写实"
        return (
            f"场景分镜插画：{scene_text[:300]}\n"
            f"风格要求：{style_suffix}；高细节、光影层次丰富、构图居中、"
            f"适合小说章节插图；禁止出现文字水印。"
        )

    def build_negative_prompt(self) -> str:
        return "低质量, 模糊, 变形, 多余手指, 文字, 水印, 签名"


class StoryboardService:
    """分镜生图调度：任务提交 / 图片保存 / 锚点双向绑定。"""

    def __init__(
        self,
        project_manager,
        task_manager=None,
        image_generator: Callable[..., Any] | None = None,
    ) -> None:
        self._pm = project_manager
        self._task_manager = task_manager
        # 真实生图回调（外部注入，如 ImageGen 连接器）；未注入时生成风格化 SVG 占位图
        self._image_generator = image_generator
        self._prompt_builder = StoryboardPromptBuilder()

    # ── 目录与锚点索引 ──────────────────────────────────────────

    def _storyboard_dir(self, project_id: str) -> Path:
        d = safe_join(self._pm.projects_dir, project_id, "assets", "storyboard")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _anchors_file(self, project_id: str) -> Path:
        return self._storyboard_dir(project_id) / "anchors.jsonl"

    async def _load_anchors(self, project_id: str) -> list[dict[str, Any]]:
        f = self._anchors_file(project_id)
        if not f.exists():
            return []
        try:
            lines = await asyncio.to_thread(f.read_text, encoding="utf-8")
        except OSError:
            return []
        anchors = []
        for line in lines.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                anchors.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return anchors

    async def _save_anchor(self, project_id: str, anchor: dict[str, Any]) -> None:
        f = self._anchors_file(project_id)

        def _append() -> None:
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(anchor, ensure_ascii=False) + "\n")

        await asyncio.to_thread(_append)

    async def _rewrite_anchors(self, project_id: str, anchors: list[dict[str, Any]]) -> None:
        f = self._anchors_file(project_id)
        payload = "".join(json.dumps(a, ensure_ascii=False) + "\n" for a in anchors)
        await asyncio.to_thread(f.write_text, payload, encoding="utf-8")

    # ── 生图任务（批次1 CommandTask 类型 GENERATE_IMAGE） ─────────

    async def submit_generate_task(
        self,
        project_id: str,
        scene_text: str,
        style_tags: list[str] | None = None,
        doc_id: str | None = None,
        text_range: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """提交分镜生图任务：封装为批次1 CommandTask(task_type=GENERATE_IMAGE)。"""
        prompt = self._prompt_builder.build_prompt(scene_text, style_tags)
        task_id = f"sb_{uuid.uuid4().hex[:12]}"

        anchor_id = f"anchor_{uuid.uuid4().hex[:12]}"
        anchor = {
            "anchor_id": anchor_id,
            "project_id": project_id,
            "doc_id": doc_id,
            "text_range": list(text_range) if text_range else None,
            "scene_text": scene_text[:500],
            "prompt": prompt,
            "image_file": None,
            "status": "PENDING",
            "created_at": _now_iso(),
        }
        await self._save_anchor(project_id, anchor)

        if self._task_manager is not None:
            from models.task import BasePipelineTask, CommandTask

            task = CommandTask(
                task_id=task_id,
                task_type="generate_image",
                raw_command=prompt,
                priority=5,
                segment_strategy="no_split",
            )
            # 附加上下文（锚点回填用，模型 extra=allow 透传）
            task.payload = {  # type: ignore[attr-defined]
                "anchor_id": anchor_id,
                "project_id": project_id,
                "negative_prompt": self._prompt_builder.build_negative_prompt(),
            }
            await self._task_manager.submit_task(task)
        else:
            # 无任务管理器时同步生成（单元测试/降级场景）
            await self.process_generate(project_id, anchor_id, prompt)

        return {"task_id": task_id, "anchor_id": anchor_id, "prompt": prompt}

    async def process_generate(self, project_id: str, anchor_id: str, prompt: str) -> str:
        """执行生图并保存至项目 assets/storyboard 目录，回填锚点。返回图片文件名。"""
        try:
            if self._image_generator is not None:
                image_bytes = await asyncio.to_thread(self._image_generator, prompt)
                if isinstance(image_bytes, str):
                    image_bytes = image_bytes.encode("utf-8")
                if not isinstance(image_bytes, (bytes, bytearray)):
                    raise TypeError("生图回调须返回 bytes/str")
                image_bytes = bytes(image_bytes)
            else:
                image_bytes = self._render_placeholder_svg(prompt)
        except Exception as exc:
            logger.warning("[Storyboard] 生图失败，使用占位图: %s", exc)
            image_bytes = self._render_placeholder_svg(prompt)

        is_svg = image_bytes[:4] == b"<svg"
        file_name = (
            f"sb_{anchor_id[-10:]}_{uuid.uuid4().hex[:6]}.svg"
            if is_svg
            else f"sb_{anchor_id[-10:]}_{uuid.uuid4().hex[:6]}.png"
        )
        target = self._storyboard_dir(project_id) / file_name
        await asyncio.to_thread(target.write_bytes, image_bytes)

        # 回填锚点状态
        anchors = await self._load_anchors(project_id)
        for a in anchors:
            if a.get("anchor_id") == anchor_id:
                a["image_file"] = file_name
                a["status"] = "DONE"
        await self._rewrite_anchors(project_id, anchors)
        logger.info("[Storyboard] 分镜图片已保存: %s", target)
        return file_name

    @staticmethod
    def _render_placeholder_svg(prompt: str) -> bytes:
        """生成风格化 SVG 占位图（真实生成器未注入时的保底，保证链路可用）。"""
        import html

        safe_prompt = html.escape(prompt[:120])
        lines = []
        for i in range(0, len(safe_prompt), 42):
            lines.append(safe_prompt[i : i + 42])
        text_elems = "".join(
            f'<tspan x="40" dy="{28 if i else 0}">{line}</tspan>'
            for i, line in enumerate(lines)
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="768" height="432">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#1e293b"/><stop offset="1" stop-color="#4c1d95"/>'
            "</linearGradient></defs>"
            '<rect width="768" height="432" fill="url(#g)"/>'
            '<text x="40" y="70" font-family="sans-serif" font-size="20" fill="#e2e8f0">'
            f'{text_elems}</text>'
            '<text x="40" y="400" font-family="sans-serif" font-size="14" fill="#94a3b8">'
            "Storyboard placeholder - 接入生图连接器后自动替换"
            "</text></svg>"
        )
        return svg.encode("utf-8")

    # ── 锚点双向绑定 ────────────────────────────────────────────

    async def list_anchors(self, project_id: str, doc_id: str | None = None) -> list[dict[str, Any]]:
        anchors = await self._load_anchors(project_id)
        if doc_id:
            anchors = [a for a in anchors if a.get("doc_id") == doc_id]
        return anchors

    async def anchors_by_range(self, project_id: str, doc_id: str, start: int, end: int) -> list[dict[str, Any]]:
        """按文本段落位置查分镜图片（段落→图片）。"""
        anchors = await self.list_anchors(project_id, doc_id)
        result = []
        for a in anchors:
            rng = a.get("text_range")
            if rng and len(rng) == 2 and start <= rng[1] and end >= rng[0]:
                result.append(a)
        return result

    async def anchors_by_image(self, project_id: str, image_file: str) -> dict[str, Any] | None:
        """按图片查文本段落（图片→段落）。"""
        anchors = await self._load_anchors(project_id)
        return next((a for a in anchors if a.get("image_file") == image_file), None)

    async def delete_anchor(self, project_id: str, anchor_id: str) -> bool:
        anchors = await self._load_anchors(project_id)
        target = next((a for a in anchors if a.get("anchor_id") == anchor_id), None)
        if target is None:
            return False
        # 删除关联图片文件（仅删除 storyboard 目录内的文件）
        image_file = target.get("image_file")
        if image_file:
            img_path = safe_join(self._storyboard_dir(project_id), image_file)
            if img_path.exists():
                img_path.unlink()
        anchors = [a for a in anchors if a.get("anchor_id") != anchor_id]
        await self._rewrite_anchors(project_id, anchors)
        return True


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
