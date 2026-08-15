import logging
from typing import Any
from models.task import Segment
from core.config_manager import config_manager

logger = logging.getLogger(__name__)

# 代码层硬计数键（非 LLM 输出，模型无法绕过）
_REWRITE_COUNT_KEY = "_rewrite_count"
_TOKEN_USAGE_KEY = "_total_tokens"

# 简易 token 估算：约 4 字符/token
_CHARS_PER_TOKEN = 4


class AgentRuntimeProtector:
    """
    Agent 运行时防护（核心算力损害防护）——代码层硬拦截，不依赖提示词。

    嵌入批次1 SegmentPipeline 执行前钩子，能力：
      1. 感性重写轮次代码硬计数（tail[_rewrite_count]，LLM 输出无法绕过）；
      2. 7-9 混沌模式强制重写轮次置 0（返回 0，pipeline 以此终结重试循环）；
      3. token 总量熔断（累计估算 token 超限 → InterruptedError）；
      4. 超时熔断由 pipeline 的 wait_for 兜底（超时不再重试）；
      5. 触发防护时向前端广播提示事件（WebSocket emotion_protect）。
    """
    def __init__(self):
        self._load_config()
        
    def _load_config(self):
        self.max_sensitive_rewrite_round = int(config_manager.get("emotion_engine.max_sensitive_rewrite_round", 2))
        self.chaotic_threshold = int(config_manager.get("emotion_engine.chaotic_threshold", 7))
        self.max_task_timeout_sec = int(config_manager.get("emotion_engine.max_task_timeout_sec", 120))
        self.max_task_total_token = int(config_manager.get("emotion_engine.max_task_total_token", 8192))

    def _enabled(self) -> bool:
        return bool(config_manager.get("feature.emotion_quantify_enable", False))

    async def _emit_protect_event(self, segment: Segment, message: str, event: str = "rewrite_block") -> None:
        """向前端广播防护提示事件（失败静默，不影响主流程）。"""
        try:
            from api.websocket import manager

            await manager.publish_task_event(
                segment.parent_task_id,
                {
                    "type": "emotion_protect",
                    "event": event,
                    "segment_id": segment.segment_id,
                    "message": message,
                },
            )
        except Exception as exc:
            logger.debug("[AgentRuntimeProtector] 防护事件广播失败: %s", exc)

    def _estimate_tokens(self, segment: Segment, tail: dict[str, Any]) -> int:
        """估算本次执行将消耗的 token（正文 + 已有尾巴体积）。"""
        payload_len = len(segment.content_payload or "")
        tail_len = len(str(tail or {}))
        return (payload_len + tail_len) // _CHARS_PER_TOKEN

    async def pre_execution_hook(
        self, segment: Segment, tail: dict[str, Any], attempt: int, max_retries: int
    ) -> int:
        """
        SegmentPipeline.execute() 执行前调用。
        返回「允许的最大重试轮次」（pipeline 必须以此值覆盖本地重试上限）。

        - 引擎总开关关闭 → 原样返回 max_retries（完全回退）；
        - 7-9 混沌模式 → 返回 0：强制关闭重写迭代（代码层硬拦截）；
        - 普通模式 → 重写计数达到上限抛 InterruptedError；
        - token 超限 → InterruptedError 熔断。
        """
        if not self._enabled():
            return max_retries

        wave_level = tail.get("wave_level", 1)
        mode = tail.get("mode", "single")

        # ── 1. 混沌模式（wave≥7 或 mode=chaotic）：强制重写轮次置 0 ──
        if wave_level >= self.chaotic_threshold or mode == "chaotic":
            logger.info(
                "[AgentRuntimeProtector] 混沌模式 (wave=%s) 硬拦截：重写迭代上限强制置 0",
                wave_level,
            )
            await self._emit_protect_event(
                segment,
                f"混沌模式 (wave_level={wave_level}) 禁止重写迭代，重试上限强制置 0",
                event="chaotic_no_rewrite",
            )
            return 0

        # ── 2. 感性重写轮次代码硬计数 ──
        # 重写轮次是「段内」语义：由 pipeline 传入的 attempt（代码层循环变量，
        # LLM 输出无法篡改）作为权威计数；tail 仅做审计快照，跨段不累计。
        rewrite_count = int(attempt)
        if rewrite_count >= self.max_sensitive_rewrite_round:
            logger.warning(
                "[AgentRuntimeProtector] 感性重写轮次达到上限 (%d)，终止重写",
                self.max_sensitive_rewrite_round,
            )
            await self._emit_protect_event(
                segment,
                f"感性重写轮次达到上限（{self.max_sensitive_rewrite_round} 次），已终止迭代",
                event="rewrite_limit",
            )
            raise InterruptedError("已到达感性重写最大轮次，停止迭代，避免算力损害")

        # 审计快照（代码计数，不跨段参与拦截）
        tail[_REWRITE_COUNT_KEY] = rewrite_count + 1

        # ── 3. token 总量熔断 ──
        total_tokens = int(tail.get(_TOKEN_USAGE_KEY, 0))
        total_tokens += self._estimate_tokens(segment, tail)
        tail[_TOKEN_USAGE_KEY] = total_tokens
        if total_tokens > self.max_task_total_token:
            logger.warning(
                "[AgentRuntimeProtector] token 熔断：累计 %d > 上限 %d",
                total_tokens, self.max_task_total_token,
            )
            await self._emit_protect_event(
                segment,
                f"token 总量熔断（{total_tokens}/{self.max_task_total_token}）",
                event="token_breaker",
            )
            raise InterruptedError("token 总量超出安全上限，任务熔断")

        return max_retries


agent_runtime_protector = AgentRuntimeProtector()
