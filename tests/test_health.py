"""健康探针与生产配置校验测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from core.health import liveness_details, readiness_details, validate_startup_configuration


def test_liveness_does_not_require_runtime_dependencies() -> None:
    assert liveness_details() == {"status": "alive"}


def test_production_requires_auth_and_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config_manager import config_manager

    monkeypatch.setenv("AIILIE_RUNTIME_ENV", "production")
    monkeypatch.setitem(config_manager._config, "security", {"require_auth": False, "auth_secret": ""})
    problems = validate_startup_configuration()
    assert "production 环境必须开启 security.require_auth" in problems
    assert "production 环境必须配置 auth_secret" in problems


@pytest.mark.asyncio
async def test_readiness_is_not_ready_before_initialization() -> None:
    app = FastAPI()
    app.state.initialization_complete = False
    app.state.initialization_error = None
    details = await readiness_details(app)
    assert details["status"] == "not_ready"
    assert details["checks"]["initialization"]["status"] == "initialization_incomplete"


@pytest.mark.asyncio
async def test_readiness_checks_database_and_workers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class Cursor:
        async def fetchone(self):
            return (1,)

    class Connection:
        async def execute(self, query: str):
            assert query == "SELECT 1"
            return Cursor()

    app = FastAPI()
    app.state.db = SimpleNamespace(_connection=Connection())
    app.state.task_manager = SimpleNamespace(_is_running=True)
    app.state.initialization_complete = True
    app.state.initialization_error = None
    monkeypatch.setattr("core.health.get_app_data_dir", lambda: tmp_path / "app_data")
    monkeypatch.setattr("core.health.get_temp_root", lambda: tmp_path / "temp")
    monkeypatch.setattr("core.health.get_workspace_dir", lambda: tmp_path / "workspace")
    # readiness 需要 asyncio.Task 的 done() 接口，直接用轻量状态替身避免启动真实 worker。
    for name in ("bg_reflection_task", "bg_queue_task", "bg_batch1_task", "bg_emotion_archive_task"):
        app.state.__setattr__(name, SimpleNamespace(done=lambda: False, cancelled=lambda: False))

    details = await readiness_details(app)
    assert details["status"] == "ready"