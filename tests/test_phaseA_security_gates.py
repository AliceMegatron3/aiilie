"""阶段A 暴露面锁定回归测试。

覆盖三类改动：
1. 启动 fail-closed 配置校验（生产必须鉴权+密钥、禁止开启代码执行、禁止 0.0.0.0 绑定；
   任何环境开了鉴权就必须有非空密钥）。
2. 代码/插件执行 fail-closed 总开关（关闭时拒绝在宿主环境执行）。
3. 密钥不再落盘明文（config/llm_provider.yaml 不得含 sk- 密钥）。
"""
from __future__ import annotations

import re

from core.config_manager import config_manager
from core.health import validate_startup_configuration
from services.code_execution import CodeExecutionService, CodePolicyError
from services.plugin_runner import JsonPluginRunner, PluginExecutionError


class _FakeManager:
    plugins_dir = None
    loaded_plugins = {}


def test_production_requires_auth_and_secret(monkeypatch):
    monkeypatch.setenv("AIILIE_RUNTIME_ENV", "production")
    monkeypatch.setenv("AIILIE_CODE_EXECUTION_ENABLED", "false")
    config_manager.reload()
    problems = validate_startup_configuration()
    joined = "; ".join(problems)
    assert "必须开启 security.require_auth" in joined
    assert "必须配置 auth_secret" in joined


def test_production_rejects_code_execution_enabled(monkeypatch):
    monkeypatch.setenv("AIILIE_RUNTIME_ENV", "production")
    monkeypatch.setenv("AIILIE_SECURITY_REQUIRE_AUTH", "true")
    monkeypatch.setenv("AIILIE_SECURITY_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("AIILIE_CODE_EXECUTION_ENABLED", "true")
    config_manager.reload()
    problems = validate_startup_configuration()
    assert any("禁止开启受限代码执行" in item for item in problems)


def test_production_rejects_dangerous_bind(monkeypatch):
    monkeypatch.setenv("AIILIE_RUNTIME_ENV", "production")
    monkeypatch.setenv("AIILIE_SECURITY_REQUIRE_AUTH", "true")
    monkeypatch.setenv("AIILIE_SECURITY_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("AIILIE_CODE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("AIILIE_SERVER_HOST", "0.0.0.0")
    config_manager.reload()
    problems = validate_startup_configuration()
    assert any("禁止绑定 0.0.0.0" in item for item in problems)


def test_auth_on_without_secret_fails_closed_any_env(monkeypatch):
    monkeypatch.setenv("AIILIE_RUNTIME_ENV", "development")
    monkeypatch.setenv("AIILIE_SECURITY_REQUIRE_AUTH", "true")
    monkeypatch.setenv("AIILIE_SECURITY_AUTH_SECRET", "")
    config_manager.reload()
    problems = validate_startup_configuration()
    assert any("必须配置 auth_secret" in item for item in problems)


def test_development_anonymous_with_no_secret_is_ok(monkeypatch):
    monkeypatch.setenv("AIILIE_RUNTIME_ENV", "development")
    monkeypatch.setenv("AIILIE_SECURITY_REQUIRE_AUTH", "false")
    monkeypatch.setenv("AIILIE_CODE_EXECUTION_ENABLED", "false")
    config_manager.reload()
    assert validate_startup_configuration() == []


def test_code_execution_refused_when_disabled(monkeypatch):
    monkeypatch.setenv("AIILIE_CODE_EXECUTION_ENABLED", "false")
    config_manager.reload()
    import asyncio

    async def _run():
        service = CodeExecutionService(db=None)
        service.workspace_root = service.temp_root
        from models.code_execution import CodeTaskRequest

        request = CodeTaskRequest(command="compileall", target="x.py")
        plan = service.approve(service.plan(request), "a-approval-id")
        await service.execute(request, plan, task_id="t1")

    try:
        asyncio.run(_run())
    except CodePolicyError as exc:
        assert "已禁用" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("执行开关关闭时应当拒绝 execute()")


def test_plugin_runner_refused_when_disabled(monkeypatch):
    monkeypatch.setenv("AIILIE_PLUGINS_EXECUTION_ENABLED", "false")
    config_manager.reload()
    import asyncio

    async def _run():
        runner = JsonPluginRunner(_FakeManager())
        await runner.run("some.plugin", {})

    try:
        asyncio.run(_run())
    except PluginExecutionError as exc:
        assert "已禁用" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("插件执行开关关闭时应当拒绝 run()")


def test_no_plaintext_api_key_in_provider_config():
    """config/llm_provider.yaml 不得再含明文 sk- 密钥。"""
    import yaml
    from utils.resource_path import get_resource_path

    raw = get_resource_path("config/llm_provider.yaml").read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", raw), "检测到疑似明文 API 密钥已提交"
    cfg = yaml.safe_load(raw) or {}
    key = str((cfg.get("deepseek", {}) or {}).get("api_key", "") or "")
    assert not key.startswith("sk-"), "api_key 不得为明文 sk- 值，请改用环境变量注入"