"""
core/exceptions.py — 统一业务异常体系
======================================
所有可预期的业务错误必须抛出自此处的类型化异常，
禁止在业务层裸抛 Exception / 裸 except Exception 吞异常。

用法：
    raise BusinessError("书籍名称不能为空", error_code="INVALID_BOOK_TITLE")
    raise NotFoundError("projects", "proj_xxx")
    raise UnauthorizedError()
    raise ForbiddenError()
    raise FileTooLargeError(actual=80*1024*1024, limit=50*1024*1024)
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """业务异常基类：所有可预期错误均应继承它。

    Attributes:
        message: 面向调用方的可读描述（会被序列化到响应 message 字段）。
        error_code: 机器可读错误码（响应 error_code 字段）。
        status_code: 对应 HTTP 状态码，默认 400。
        details: 可选的附加信息（校验明细等）。
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "BUSINESS_ERROR",
        status_code: int = 400,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details


class NotFoundError(AppError):
    """资源不存在 → 404。"""

    def __init__(self, resource: str, resource_id: str | None = None) -> None:
        suffix = f": {resource_id}" if resource_id else ""
        super().__init__(
            f"{resource}不存在{suffix}",
            error_code="NOT_FOUND",
            status_code=404,
        )


class ConflictError(AppError):
    """资源冲突（重复提交、状态冲突等）→ 409。"""

    def __init__(self, message: str, *, error_code: str = "CONFLICT") -> None:
        super().__init__(message, error_code=error_code, status_code=409)


class ValidationError(AppError):
    """入参校验失败 → 422。"""

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(
            message,
            error_code="VALIDATION_ERROR",
            status_code=422,
            details=details,
        )


class UnauthorizedError(AppError):
    """未认证 / 凭证无效 → 401。"""

    def __init__(self, message: str = "未认证或凭证无效") -> None:
        super().__init__(message, error_code="UNAUTHORIZED", status_code=401)


class ForbiddenError(AppError):
    """已认证但无权限 → 403。"""

    def __init__(self, message: str = "没有权限执行该操作") -> None:
        super().__init__(message, error_code="FORBIDDEN", status_code=403)


class FileTooLargeError(AppError):
    """上传文件超过大小上限 → 413。"""

    def __init__(self, limit: int, actual: int | None = None) -> None:
        msg = f"文件大小超过上限（最大 {limit // (1024 * 1024)}MB）"
        super().__init__(
            msg,
            error_code="FILE_TOO_LARGE",
            status_code=413,
            details={"limit_bytes": limit, "actual_bytes": actual},
        )


class UnsupportedFileTypeError(AppError):
    """上传文件扩展名不在白名单 → 415。"""

    def __init__(self, allowed: list[str] | None = None) -> None:
        suffix = f"，允许: {', '.join(allowed)}" if allowed else ""
        super().__init__(
            f"不支持的文件类型{suffix}",
            error_code="UNSUPPORTED_FILE_TYPE",
            status_code=415,
        )


class PathTraversalError(AppError):
    """路径越界（不在工作区根目录内）→ 400。"""

    def __init__(self, message: str = "路径必须位于工作区根目录内") -> None:
        super().__init__(
            message,
            error_code="PATH_OUT_OF_WORKSPACE",
            status_code=400,
        )


class CloudServiceDisabledError(AppError):
    """云端功能未启用（未配置密钥）→ 503。"""

    def __init__(self, feature: str = "云端大模型") -> None:
        super().__init__(
            f"{feature}功能未启用：未配置有效的 API 密钥",
            error_code="CLOUD_SERVICE_DISABLED",
            status_code=503,
        )


class InternalError(AppError):
    """非预期内部错误（全局兜底）→ 500。"""

    def __init__(self, message: str = "服务器内部故障，请查看后台日志") -> None:
        super().__init__(
            message,
            error_code="INTERNAL_ERROR",
            status_code=500,
        )
