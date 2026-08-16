"""
services/reflection_engine.py — 临时文件辅助反思引擎 (Phase 10)
===================================================
通过写入大型临时聚合文件，减少 LLM 在多次交叉对比中的 Token 开销。

【0-2 修复】去除硬编码反思结论：
- 新增 set_model_client()，由 bootstrap 在装配时注入全局 LLM 客户端；
- execute_final_reflection 优先调用注入的模型客户端生成真实反思总结；
- 未注入或调用失败时降级回占位结论（保持原调用链不中断、不抛异常）。
"""
from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Any

from core.temp_manager import temp_manager
from services.prompt_template_manager import prompt_manager

logger = logging.getLogger(__name__)


class ReflectionEngine:
    def __init__(self) -> None:
        # 注入的模型客户端（DeepSeekClient / ModelDispatcher 等具有 generate_completion 的实例）。
        self._model_client: Any = None

    def set_model_client(self, client: Any) -> None:
        """注入大模型客户端（由 bootstrap 在装配阶段调用）。

        Args:
            client: 具有 generate_completion(prompt, **kwargs) 接口的客户端实例；
                    传 None 时清除注入，恢复降级模式。
        """
        self._model_client = client

    async def execute_final_reflection(self, project_id: str, input_materials: list[str]) -> str:
        """
        利用文件大聚合，执行最终的反思总结，以大幅节省 Token 开销。
        """
        # 1. 创建该项目的临时工作区
        workspace = temp_manager.create_workspace(
            book_id=project_id,
            function_type="reflection"
        )

        try:
            logger.info(f"[ReflectionEngine] 正在利用大硬盘缓冲多维反思数据... 工作区: {workspace}")

            # 2. 生成一个巨大的综合文件，避免塞进内存
            synthesis_file = workspace / "reflection_synthesis.tmp"

            with open(synthesis_file, "w", encoding="utf-8") as f:
                f.write("=== 反思聚合材料 ===\n\n")
                for idx, material in enumerate(input_materials):
                    f.write(f"--- 材料 {idx + 1} ---\n{material}\n\n")

            # 3. 读取聚合后的材料文本（截断到安全长度，避免全文超限）
            with open(synthesis_file, "r", encoding="utf-8") as f:
                aggregated = f.read()

            logger.info(
                f"[ReflectionEngine] 聚合文件就绪 ({synthesis_file.stat().st_size} bytes)。"
            )

            if self._model_client is not None:
                try:
                    prompt = prompt_manager.render(
                        "reflection_learning_governance",
                        {
                            "reflection_scope": "project",
                            "quantization_data": aggregated[:12000],
                            "character_profiles": "[]",
                            "logic_cards": "[]",
                            "generated_content": "[]",
                            "author_feedback": "[]",
                            "failure_logs": "[]",
                            "active_skills": "[]",
                        },
                    )
                    conclusion = await self._model_client.generate_completion(
                        prompt, temperature=0.4, max_tokens=1600
                    )
                    parsed = self._parse_json_object(conclusion)
                    if parsed is not None:
                        logger.info("[ReflectionEngine] 大模型反思完成并通过 JSON 校验。")
                        return json.dumps(parsed, ensure_ascii=False)
                    raise ValueError("反思模型返回为空或非 JSON")
                except Exception as exc:
                    logger.error(
                        "[ReflectionEngine] 模型反思调用失败，返回待复核结果: %s", exc
                    )
            else:
                logger.warning(
                    "[ReflectionEngine] 未注入模型客户端，返回待复核结果。"
                )

            # 降级必须显式标记，不能把模型不可用伪装成有效反思。
            return json.dumps(
                {
                    "reflection_id": f"reflection_{project_id}",
                    "scope": "project",
                    "status": "NEEDS_REVIEW",
                    "observations": [],
                    "rule_candidates": [],
                    "skill_candidates": [],
                    "plugin_proposals": [],
                    "author_questions": ["模型不可用或返回格式无效，尚未形成可执行反思。"],
                },
                ensure_ascii=False,
            )

        finally:
            # 4. 任务结束，精准销毁对应书籍的这一个临时任务空间
            temp_manager.cleanup_workspace(workspace)

    @staticmethod
    def _parse_json_object(raw: Any) -> dict[str, Any] | None:
        """解析模型 JSON，兼容 markdown 代码块，但拒绝非对象结果。"""
        if not isinstance(raw, str) or not raw.strip():
            return None
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


# 单例
reflection_engine = ReflectionEngine()
