"""
tests/test_security.py — 安全模块单元测试
==========================================
覆盖：令牌签发/校验往返、过期判定、篡改检测、云端门控。
"""
from __future__ import annotations

import time

import pytest

from core.security import (
    generate_api_token,
    verify_api_token,
    is_cloud_enabled,
)

pytestmark = pytest.mark.asyncio


async def test_token_roundtrip():
    token = generate_api_token("test_client")
    assert verify_api_token(token) == "test_client"


async def test_invalid_token_rejected():
    assert verify_api_token("garbage") is None
    assert verify_api_token("a.b.c") is None
    assert verify_api_token("") is None


async def test_tampered_token_rejected():
    token = generate_api_token("test_client")
    payload, sig = token.split(".")
    # 篡改载荷：签名必须不匹配
    forged = f"{payload[:-1]}X.{sig}"
    assert verify_api_token(forged) is None


async def test_expired_token_rejected():
    # 直接以负 TTL 签发立即过期的令牌
    token = generate_api_token("expired_client", ttl=-10)
    assert verify_api_token(token) is None


async def test_cloud_gate_disabled_without_key(monkeypatch):
    from core.config_manager import config_manager

    monkeypatch.setitem(
        config_manager._config,
        "llm_provider",
        {"deepseek": {"enable_switch": True, "api_key": ""}},
    )
    assert is_cloud_enabled() is False

    monkeypatch.setitem(
        config_manager._config["llm_provider"],  # type: ignore[index]
        "deepseek",
        {"enable_switch": True, "api_key": "sk-xxx"},
    )
    assert is_cloud_enabled() is True
