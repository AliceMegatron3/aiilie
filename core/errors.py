"""core/errors.py — 统一错误响应（Batch 7：禁止把 `str(exception)` 返回客户端）。

提供：
- `AppError` ：携带 error_code 的领域异常；由 main 的异常处理器转为结构化 fail 包络。
- `http_error(status_code, error_code, message)` ：构造 HTTPException 的集中入口，
  返回**安全、无原始异常字符串**的 detail；上层不得再 embed `str(exception)`。
"""
from __future__ import annotations

from fastapi import HTTPException


class AppError(RuntimeError):
    """携带错误码的业务异常。"""

    def __init__(self, message: str, error_code: str = "BUSINESS_ERROR", details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


# 服务器内部错误的安全兜底文案（不暴露异常内部细节）
SERVER_ERROR_MESSAGE = "服务器内部错误，请查看后台日志"


def http_error(
    status_code: int,
    error_code: str,
    message: str = SERVER_ERROR_MESSAGE,
) -> HTTPException:
    """集中构造 `HTTPException`，detail 恒为安全 message。

    Batch 7 红线：HTTPException.detail 一律来自调用方提供的安全文案，绝不 use
    `str(exc)`；调用方如需记录原始异常，自行 logger。
    """
    if not message or not message.strip():
        message = SERVER_ERROR_MESSAGE
    return HTTPException(status_code=status_code, detail=message)