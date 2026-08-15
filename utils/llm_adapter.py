"""
utils/llm_adapter.py — 统一大语言模型适配层
===================================================
封装大语言模型客户端调用，隔离具体供应商实现。
新增 DeepSeek API 接入能力，提供标准化、带超时与重试机制的调用接口。

【专项1修复】重试错误分类：
- 可重试错误：429 限流 / 5xx 服务端 / 网络/连接/读取超时（幂等，可安全重试）
- 不可重试错误：400/401/403/404/422 等客户端错误（重试无意义，立即终止）
仅对可重试错误进入指数退避重试，从根源消除「无差别重试」。
"""
from __future__ import annotations
import logging
import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any
import httpx
logger = logging.getLogger(__name__)

class ModelErrorCategory(str, Enum):
    """模型调用错误分类（专项1新增，供上层判断与日志归因）。"""
    RETRYABLE = "retryable"        # 可重试：限流/服务端/网络/超时
    NON_RETRYABLE = "non_retryable"  # 不可重试：客户端错误（鉴权/参数/模型缺失）
    UNKNOWN = "unknown"            # 未知异常

# 不可重试的 HTTP 状态码（重试无意义，立即终止，避免重复请求/重复计费）
_NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 405, 409, 413, 415, 422})
# 可重试的 HTTP 状态码
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# httpx 中常见的网络层可重试异常类型
_RETRYABLE_NETWORK_EXC = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.NetworkError,
)

def classify_model_error(exc: BaseException) -> tuple[ModelErrorCategory, str]:
    """
    对模型调用异常进行分类（专项1新增）。

    Returns:
        (category, reason)：reason 为面向日志的人工可读原因。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _RETRYABLE_STATUS:
            return ModelErrorCategory.RETRYABLE, f"HTTP_{status}_可重试(限流/服务端)"
        if status in _NON_RETRYABLE_STATUS:
            return ModelErrorCategory.NON_RETRYABLE, f"HTTP_{status}_不可重试(客户端错误)"
        return ModelErrorCategory.RETRYABLE, f"HTTP_{status}_其他(默认按可重试处理)"
    if isinstance(exc, _RETRYABLE_NETWORK_EXC):
        return ModelErrorCategory.RETRYABLE, f"网络层可重试异常: {exc.__class__.__name__}"
    return ModelErrorCategory.UNKNOWN, f"未知异常类型: {exc.__class__.__name__}"

class BaseLLMClient(ABC):
    """大语言模型客户端统一基类"""
    
    @abstractmethod
    async def generate_completion(self, prompt: str, **kwargs: Any) -> str:
        """执行模型推理并返回标准格式字符串"""
        pass
class BaseLLMClient(ABC):
    """大语言模型客户端统一基类"""
    
    @abstractmethod
    async def generate_completion(self, prompt: str, **kwargs: Any) -> str:
        """执行模型推理并返回标准格式字符串"""
        pass
class DeepSeekClient(BaseLLMClient):
    """
    DeepSeek API 专用客户端。
    固定对接 https://api.deepseek.com/v1 标准接口。
    实现自动重试与异常封装，保证上层调用格式透明对齐。
    """
    
    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.deepseek.com/v1",
        model_name: str = "deepseek-r1",
        request_timeout: int = 60,
        max_retry_times: int = 2
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model_name = model_name
        self.request_timeout = request_timeout
        self.max_retry_times = max_retry_times
    async def generate_completion(self, prompt: str, **kwargs: Any) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}]
        }
        
        # 提取传入的动态参数（如 temperature），否则使用默认
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
            
        endpoint = f"{self.api_base}/chat/completions"
        
        # 【专项1修复】基于错误分类的重试：仅对可重试错误进入重试流程，
        # 不可重试错误（4xx 客户端错误）立即终止，杜绝无差别重试造成的
        # 重复请求/重复计费与长时间无效等待。
        for attempt in range(self.max_retry_times + 1):
            try:
                async with httpx.AsyncClient(timeout=float(self.request_timeout)) as client:
                    resp = await client.post(endpoint, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    
                    # 标准化解析响应结构
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
                    return ""
            except httpx.HTTPStatusError as e:
                category, reason = classify_model_error(e)
                logger.warning(
                    "[DeepSeek] HTTP 异常 (状态码: %s) 分类=%s reason=%s: %s",
                    e.response.status_code, category.value, reason, e.response.text,
                )
                if category == ModelErrorCategory.NON_RETRYABLE:
                    logger.info(
                        "[DeepSeek] 不可重试错误（%s），终止重试（已尝试 %d/%d）",
                        reason, attempt + 1, self.max_retry_times + 1,
                    )
                    raise
                # 可重试：非最后一次则等待后重试
                if attempt < self.max_retry_times:
                    await asyncio.sleep(2 ** attempt)
                continue
            except _RETRYABLE_NETWORK_EXC as e:
                category, reason = classify_model_error(e)
                logger.warning(
                    "[DeepSeek] 网络层异常 分类=%s reason=%s: %s",
                    category.value, reason, e,
                )
                if attempt < self.max_retry_times:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return "【离线安全保护】无网络或接口不可用，当前大模型提取/反思功能已暂停。"
            except Exception as e:
                category, reason = classify_model_error(e)
                logger.error(
                    "[DeepSeek] 调用发生未知异常 分类=%s reason=%s: %s",
                    category.value, reason, e,
                )
                # 未知异常：保守重试一次（幂等场景），仍失败则终止
                if attempt < self.max_retry_times:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return "【离线安全保护】无网络或接口不可用，当前大模型提取/反思功能已暂停。"
        
        return ""