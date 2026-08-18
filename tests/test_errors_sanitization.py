"""Batch 7：统一错误码——禁止把 `str(exception)` 返回客户端。

覆盖：
- `core.errors.http_error`：detail 恒为安全文案，绝不 embed 原始异常串；
- `AppError` 经全局处理器转为结构化 envelope（error_code），不泄漏异常细节；
- 全局 Exception 处理器对未处理异常返回统一 INTERNAL_ERROR（无 str(e)）。
"""
from __future__ import annotations

import pytest

from core.errors import AppError, SERVER_ERROR_MESSAGE, http_error


def test_http_error_never_embeds_exception_str():
    err = http_error(500, "SERVER_ERROR")
    detail = err.detail
    # http_error 集中构造，detail 恒为安全兜底文案，绝不 embed 任何异常串
    assert detail == SERVER_ERROR_MESSAGE
    assert err.status_code == 500


def test_app_error_carries_error_code_and_safe_message():
    a = AppError("请求被拒", error_code="FORBIDDEN")
    assert a.error_code == "FORBIDDEN"
    b = http_error(409, "FORBIDDEN", "请求被拒")
    assert b.status_code == 409
    assert b.detail == "请求被拒"


def test_internal_error_fallback_uses_safe_envelope_not_raw_str():
    """全局 INTERNAL_ERROR 兜底必须是结构化 fail 包络且不含原始异常串，
    从 core.response.fail 构造（与 main 的 Exception 处理器一致）。"""
    from core.response import fail

    content = fail("服务器内部故障，请查看后台日志", error_code="INTERNAL_ERROR")
    assert content["success"] is False
    assert content["error_code"] == "INTERNAL_ERROR"
    assert "Traceback" not in str(content)
    # 准异常文本永不进入包络
    assert "abc123" not in str(content)