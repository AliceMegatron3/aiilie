"""
services/segment_execution_hook.py — 批次1分段执行的真实模型钩子
================================================================
阶段1主链路真实化（内核与插件平台架构讨论稿_20260816 第三章修复顺序第1步）：

背景：SegmentPipeline 生产装配此前从未注入执行钩子，长任务创作一直
走 services/segment_pipeline.py 的占位钩子，产出 "[PLACEHOLDER] 已处理内容"。

本模块提供钩子工厂 build_model_execution_hook：
- 经 ModelDispatcher 统一调度（云端优先/本地兜底由 dispatcher 的
  think/rapid 分支决定），自动携带项目绑定的卡片上下文；
- 遵守流水线钩子契约 (content_payload, tail_context, model_source,
  retry_prompt) -> JSON 字符串 {"result_content": ..., "new_tail": ...}；
- tail_context 中若携带 project_id（提交任务时注入首段尾巴），
  则透传给 dispatcher 以启用书库卡片上下文注入；
- 模型返回空内容视为失败（抛异常走流水线重试），拒绝静默占位输出。

设计约束：dispatcher 在 setup_quantification 阶段才装配（晚于
setup_batch1_engine），因此钩子通过延迟 getter 解引用，不在工厂
创建时绑定实例。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 延迟解引用 dispatcher 的回调类型（避免装配顺序耦合）
DispatcherGetter = Callable[[], Any]


def build_model_execution_hook(dispatcher_getter: DispatcherGetter):
    """构造接入真实模型调度的分段执行钩子。

    dispatcher_getter: 每次执行时调用以获取当前 ModelDispatcher 实例
    （如 lambda: app.state.model_dispatcher），返回 None 视为未装配。
    """

    async def _model_execution_hook(
        content_payload: str,
        tail_context: dict[str, Any],
        model_source: str,
        retry_prompt: str | None = None,
    ) -> str:
        dispatcher = dispatcher_getter()
        if dispatcher is None:
            # 显式失败而非退回占位输出：占位文本会污染创作链路与学习遥测
            raise RuntimeError("模型调度器未装配，分段执行拒绝产生占位输出")

        prompt = content_payload
        if retry_prompt:
            prompt = f"{retry_prompt}\n\n{content_payload}"

        # project_id 由任务提交时注入首段 tail_context，随尾巴接力传递
        project_id = (tail_context or {}).get("project_id") or None

        # 模型来源 -> 调度模式：云端/混合走 think（云端优先+本地兜底），
        # 本地保持默认（rapid：本地优先+云端补救），尊重提交时的算力选择
        source = str(model_source or "").strip().lower()
        override_mode = "think" if source in ("cloud", "hybrid") else None

        logger.info(
            "分段真实执行钩子: model_source=%s, override_mode=%s, project_id=%s, retry=%s",
            model_source, override_mode, project_id, retry_prompt is not None,
        )
        result_text = await dispatcher.dispatch(
            prompt,
            project_id=project_id,
            override_mode=override_mode,
        )
        result_text = str(result_text or "").strip()
        if not result_text:
            raise ValueError("模型返回空内容，拒绝作为分段结果")

        new_tail = dict(tail_context or {})
        new_tail["processed_count"] = int(new_tail.get("processed_count", 0)) + 1
        new_tail["last_model_source"] = str(model_source)
        new_tail["last_content_length"] = len(result_text)
        return json.dumps(
            {"result_content": result_text, "new_tail": new_tail},
            ensure_ascii=False,
        )

    return _model_execution_hook
