"""阶段C：Feature Flag 启动/运行时语义统一回归。

统一后：feature 路由始终挂载，由 handler 内运行时 _require_feature/_feature_gate
按请求时 config 裁决（关闭返回明确 disabled 而非 404），运行时切换无需重启。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from core.config_manager import config_manager


def test_feature_routers_always_mounted_include_novel_agent():
    """novel_multi_agent_enable 默认关闭，但 /novel-agent 路由仍应挂载（运行时 gate 而非挂载门控）。"""
    from api.api_router import api_router
    from tests.conftest import flatten_api_router

    paths = {r.path for r in flatten_api_router(api_router)}
    assert any("/novel-agent/" in p for p in paths)


def test_novel_agent_runtime_gate_blocks_when_disabled(monkeypatch):
    """feature off → 运行时 gate 抛 400；on → 放行。"""
    feature_cfg = config_manager._config.setdefault("feature", {})

    monkeypatch.setitem(feature_cfg, "novel_multi_agent_enable", False)
    from api.novel_agent import _feature_gate

    with pytest.raises(HTTPException) as ei:
        _feature_gate()
    assert ei.value.status_code == 400

    monkeypatch.setitem(feature_cfg, "novel_multi_agent_enable", True)
    assert _feature_gate() is None  # 开启时放行


def test_settings_features_returns_all_switches(monkeypatch):
    """GET /settings/features 暴露门控所需开关（含新增的 branch/novel/storyboard/tts 等）。"""
    from api.settings import _FEATURE_KEYS

    required = {
        "emotion_quantify_enable", "branch_version_enable", "novel_multi_agent_enable",
        "storyboard_enable", "tts_enable", "deep_thinking_enable", "timeline_enable",
    }
    assert required.issubset(set(_FEATURE_KEYS))


def test_feature_disabled_contract():
    """未接入 provider 的能力统一返回结构化 DISABLED（501 + X-Feature-Status 头）。"""
    import json

    from core.feature_status import feature_disabled

    resp = feature_disabled("storyboard.image_generation", "provider_not_configured")
    assert resp.status_code == 501
    assert resp.headers.get("X-Feature-Status") == "disabled"
    body = json.loads(resp.body.decode("utf-8"))
    assert body == {
        "status": "DISABLED",
        "feature": "storyboard.image_generation",
        "reason": "provider_not_configured",
    }


def test_storyboard_provider_availability():
    """未注入 image_generator 时不可用（generate 应返回 DISABLED 而非伪造任务）。"""
    class _PM:
        projects_dir = None

    from services.storyboard import StoryboardService

    svc = StoryboardService(_PM())
    assert svc.image_generator_available is False
    svc2 = StoryboardService(_PM(), image_generator=lambda prompt: b"png")
    assert svc2.image_generator_available is True


def test_tts_provider_availability():
    """未配置 azure/elevenlabs 密钥 → provider 不可用（返回 DISABLED）。"""
    from services.tts import TTSDispatcher

    disp = TTSDispatcher()
    assert disp.provider_available("azure") is False
    assert disp.provider_available("elevenlabs") is False
    assert disp.provider_available("no-such-engine") is False