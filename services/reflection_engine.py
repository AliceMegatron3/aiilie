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
import asyncio
from pathlib import Path
from typing import Any

from core.temp_manager import temp_manager

logger = logging.getLogger(__name__)


class ReflectionEngine:
    def __init__(self) -> None:
        # 注入的模型客户端（DeepSeekClient / ModelDispatcher 等具有 generate_completion 的实例）。
        # 未注入时 execute_final_reflection 自动降级为占位结论。
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
                    prompt = (
                        "你是一名资深小说创作反思顾问。请基于以下聚合材料，"
                        "提炼 3-5 条可执行、可复用的创作优化结论。\n\n"
                        f"{aggregated[:12000]}"
                    )
                    conclusion = await self._model_client.generate_completion(
                        prompt, temperature=0.4, max_tokens=1024
                    )
                    if conclusion and conclusion.strip():
                        logger.info(f"[ReflectionEngine] 大模型反思完成。")
                        return conclusion.strip()
                except Exception as exc:
                    logger.error(
                        "[ReflectionEngine] 模型反思调用失败，降级为占位结论: %s", exc
                    )
            else:
                logger.warning(
                    "[ReflectionEngine] 未注入模型客户端，降级为占位结论（如需真实反思请注入 llm_client）。"
                )

            # 降级：保留原有占位结论，保证老调用方不中断
            final_conclusion = "【反思总结】基于综合材料，应当减少无用的水文字，增加紧凑的动作描写。"
            logger.info(f"[ReflectionEngine] 思考完成（降级模式）。")
            return final_conclusion

        finally:
            # 4. 任务结束，精准销毁对应书籍的这一个临时任务空间
            temp_manager.cleanup_workspace(workspace)


# 单例
reflection_engine = ReflectionEngine()
