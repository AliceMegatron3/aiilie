"""
core/response.py — 统一 API 返回结构
======================================
所有 /api/v1 接口统一返回结构：
    {
        "success": true | false,
        "data": <payload>,
        "message": "人类可读信息",
        "error_code": null | "机器可读错误码"
    }

v1 保持既有字段兼容（个别旧接口仍直出模型），新增接口一律使用本模块。
/api/v2（规划中）将强制统一此结构并收敛所有旧响应。
"""
from __future__ import annotations

from typing import Any


def ok(data: Any = None, message: str = "success") -> dict[str, Any]:
    """构造成功响应。"""
    return {
        "success": True,
        "data": data,
        "message": message,
        "error_code": None,
    }


def fail(
    message: str,
    *,
    error_code: str = "BUSINESS_ERROR",
    data: Any = None,
) -> dict[str, Any]:
    """构造失败响应体（供异常处理器复用）。"""
    return {
        "success": False,
        "data": data,
        "message": message,
        "error_code": error_code,
    }
