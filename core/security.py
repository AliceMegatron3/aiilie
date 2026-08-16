"""
core/security.py — 鉴权与云端门控
==================================
1. TokenAuth：基于标准库 HMAC 的签名令牌（无外部依赖），
   支持 Authorization: Bearer <token> 与 X-API-Token 两种携带方式。
2. 配置 security.require_auth=true 时强制鉴权（生产环境建议开启）；
   关闭时保持开发模式匿名放行并记录警告。
3. cloud_gate：未配置密钥时禁用云端功能（统一密钥门控）。

安全声明：桌面应用场景下 Token 用于"防误用/防跨域滥用"，并非高强度加密体系；
生产部署时应将 secret 置于环境变量并启用 HTTPS。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import time
import os
from typing import Callable

from fastapi import Depends, HTTPException, Request

from core.config_manager import config_manager
from core.exceptions import AppError, UnauthorizedError

logger = logging.getLogger(__name__)

# 令牌有效期：7 天
_TOKEN_TTL_SECONDS = 7 * 24 * 3600

# 落盘密钥文件（generate_secret_if_missing 生成，位于用户数据目录）
_SECRET_FILE_NAME = "auth_secret"

# 开发模式（require_auth=false 且未配置 secret）下的会话内随机密钥：
# 不可预测、重启即失效，替代原硬编码可伪造常量。
_SESSION_FALLBACK_SECRET: str | None = None


def _read_secret_file() -> str | None:
    """从用户数据目录读取落盘密钥（若存在）。"""
    try:
        from core.path_resolver import get_app_data_dir

        secret_file = get_app_data_dir() / _SECRET_FILE_NAME
        if secret_file.exists():
            return secret_file.read_text(encoding="utf-8").strip() or None
    except Exception:
        pass
    return None


def _get_secret() -> str:
    """获取签名密钥，优先级：环境变量/配置 > 落盘文件 > 会话内随机（仅开发模式）。

    - 未配置任何密钥且 require_auth=true：抛 AppError fail-fast，阻止以可伪造状态启动。
    - 未配置任何密钥且 require_auth=false：返回会话内随机密钥（重启失效，仅本地签名一致性）。
    """
    global _SESSION_FALLBACK_SECRET

    # 容器/生产环境通过环境变量注入，优先级必须与健康检查保持一致。
    secret = os.environ.get("AIILIE_SECURITY_AUTH_SECRET") or config_manager.get("security.auth_secret") or ""
    if secret:
        return str(secret)

    secret = _read_secret_file()
    if secret:
        return secret

    if bool(config_manager.get("security.require_auth", False)):
        raise AppError(
            "未配置 security.auth_secret 且已开启鉴权，请通过环境变量 "
            "AIILIE_SECURITY_AUTH_SECRET 注入密钥或调用 generate_secret_if_missing() 生成",
            error_code="AUTH_SECRET_MISSING",
            status_code=500,
        )

    if _SESSION_FALLBACK_SECRET is None:
        _SESSION_FALLBACK_SECRET = secrets.token_urlsafe(32)
        logger.warning(
            "[Security] 未配置 security.auth_secret，使用会话内随机密钥（重启失效，仅限开发模式）"
        )
    return _SESSION_FALLBACK_SECRET


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def generate_api_token(identity: str = "app_client", ttl: int = _TOKEN_TTL_SECONDS) -> str:
    """签发一个带过期时间的签名令牌，返回 base64 字符串。"""
    payload = f"{identity}:{int(time.time()) + ttl}"
    payload_b64 = _b64url_encode(payload.encode("utf-8"))
    sig = hmac.new(
        _get_secret().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify_api_token(token: str) -> str | None:
    """校验令牌，返回 identity；无效或过期返回 None。"""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected = hmac.new(
            _get_secret().encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64url_encode(expected), sig_b64):
            return None
        payload = _b64url_decode(payload_b64).decode("utf-8")
        identity, _, expiry = payload.rpartition(":")
        if int(expiry) < int(time.time()):
            logger.warning("[Security] 令牌已过期")
            return None
        return identity
    except (ValueError, UnicodeDecodeError, KeyError, binascii.Error, OverflowError):
        return None


def create_auth_dependency() -> Callable:
    """构造 FastAPI 鉴权依赖（闭包持有配置快照，避免每次请求读配置）。"""
    raw_require_auth = os.environ.get("AIILIE_SECURITY_REQUIRE_AUTH")
    if raw_require_auth is None:
        require_auth = bool(config_manager.get("security.require_auth", False))
    else:
        require_auth = raw_require_auth.strip().lower() in {"1", "true", "yes", "on"}

    async def authenticate(request: Request) -> str:
        auth_header = request.headers.get("Authorization", "")
        api_token = request.headers.get("X-API-Token", "")

        token: str | None = None
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):].strip()
        elif api_token:
            token = api_token.strip()

        if token:
            identity = verify_api_token(token)
            if identity:
                return identity
            raise UnauthorizedError("令牌无效或已过期")

        if require_auth:
            raise UnauthorizedError("缺少鉴权令牌（security.require_auth 已开启）")

        logger.warning(
            "[Security] 未携带鉴权头，以匿名身份访问（开发模式放行）: %s",
            request.url.path,
        )
        return "anonymous_user"

    return authenticate


def is_cloud_enabled() -> bool:
    """云端功能统一门控：llm_provider 启用开关 + 密钥有效性双条件。

    【专项1修复】配置矛盾态（enable_switch=true 且 api_key 为空）显式处理：
    自动禁用云端功能并打印告警，而非报错终止——
    桌面单用户场景需保障本地离线可用性，与 is_cloud_enabled 既有门控语义一致。
    """
    enable = bool(config_manager.get("llm_provider.deepseek.enable_switch", False))
    key = str(config_manager.get("llm_provider.deepseek.api_key", "") or "").strip()
    if enable and not key:
        logger.warning(
            "[Security] 检测到矛盾配置：llm_provider.deepseek.enable_switch=true "
            "但 api_key 为空，云端功能已自动禁用（离线模式）"
        )
        return False
    return enable and bool(key)


def generate_secret_if_missing() -> str:
    """首次启动时若未配置 auth_secret，生成随机密钥并落盘（持久化到用户数据目录）。

    落盘文件路径：`get_app_data_dir()/auth_secret`，内容为 url-safe base64 随机串。
    返回最终生效的密钥（已有配置则原样返回，不覆盖）。
    """
    environment_secret = os.environ.get("AIILIE_SECURITY_AUTH_SECRET") or ""
    if environment_secret:
        return environment_secret
    existing = config_manager.get("security.auth_secret") or ""
    if existing:
        return str(existing)

    file_secret = _read_secret_file()
    if file_secret:
        return file_secret

    secret = secrets.token_urlsafe(32)
    try:
        from core.path_resolver import get_app_data_dir

        secret_file = get_app_data_dir() / _SECRET_FILE_NAME
        secret_file.write_text(secret, encoding="utf-8")
        logger.info("[Security] 已生成并落盘鉴权密钥: %s", secret_file)
    except Exception as exc:  # pragma: no cover
        logger.error("[Security] 落盘鉴权密钥失败，回退会话内密钥: %s", exc)
        global _SESSION_FALLBACK_SECRET
        _SESSION_FALLBACK_SECRET = secret
    return secret


__all__ = [
    "generate_api_token",
    "verify_api_token",
    "create_auth_dependency",
    "is_cloud_enabled",
    "generate_secret_if_missing",
]
