"""应用健康探针与启动配置校验。

健康探针只暴露脱敏后的运行状态：
* liveness 表示进程仍可响应，不访问数据库或模型；
* readiness 表示应用底座已完成初始化，且可以接受业务请求。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from core.config_manager import config_manager
from core.path_resolver import get_app_data_dir, get_temp_root, get_workspace_dir

logger = logging.getLogger("ai_v4_health")

_WORKER_STATE_NAMES = (
    "bg_reflection_task",
    "bg_queue_task",
    "bg_batch1_task",
    "bg_emotion_archive_task",
)


def runtime_environment() -> str:
    """返回当前运行环境，环境变量优先于配置文件。"""
    return str(
        os.environ.get(
            "AIILIE_RUNTIME_ENV",
            config_manager.get("runtime.environment", "development"),
        )
        or "development"
    ).strip().lower()


def _effective_require_auth() -> bool:
    """解析最终生效的 require_auth（环境变量优先于配置）。"""
    raw = os.environ.get("AIILIE_SECURITY_REQUIRE_AUTH")
    if raw is None:
        return config_manager.get_bool("security.require_auth", False)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _effective_auth_secret() -> str:
    """读取生效的 auth_secret（环境变量 > 配置 > 落盘文件）。"""
    return str(
        os.environ.get(
            "AIILIE_SECURITY_AUTH_SECRET",
            config_manager.get("security.auth_secret", ""),
        )
        or ""
    ).strip()


def validate_startup_configuration() -> list[str]:
    """校验生产模式的关键安全配置，返回脱敏后的问题列表。

    阶段A fail-closed 规则：
    - 生产环境必须开启 require_auth，且必须配置 auth_secret；
    - 只要 require_auth=true，就必须有非空 auth_secret（防止匿名/可伪造启动，任何环境生效）；
    - 生产环境禁止开启受限代码执行（隔离 worker 建成前 fail-closed）；
    - 生产环境禁止绑定 0.0.0.0 暴露到所有接口。
    """
    env = runtime_environment()
    problems: list[str] = []

    require_auth = _effective_require_auth()
    auth_secret = _effective_auth_secret()

    if env in {"production", "prod"}:
        if not require_auth:
            problems.append("production 环境必须开启 security.require_auth")
        if not auth_secret:
            problems.append("production 环境必须配置 auth_secret")
        if config_manager.get_bool("code_execution.enabled", False):
            problems.append("production 环境禁止开启受限代码执行（隔离 worker 未就绪）")
        bind_host = str(
            os.environ.get(
                "AIILIE_SERVER_HOST",
                config_manager.get("server.host", "127.0.0.1"),
            )
            or ""
        ).strip().lower()
        if bind_host in {"0.0.0.0", "::"}:
            problems.append("production 环境禁止绑定 0.0.0.0/:: 到所有接口")

    # fail-closed：任何环境下，开了鉴权就必须有非空密钥
    if require_auth and not auth_secret:
        problems.append(
            "security.require_auth=true 时必须配置 auth_secret（AIILIE_SECURITY_AUTH_SECRET 环境变量）"
        )

    return problems


def _directory_writable(path: Path) -> bool:
    """确认目录可创建且可写，不记录目录中的用户内容。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".healthcheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


async def _database_check(app: FastAPI) -> tuple[bool, str]:
    db = getattr(app.state, "db", None)
    connection = getattr(db, "_connection", None)
    if db is None or connection is None:
        return False, "database_not_initialized"
    try:
        cursor = await connection.execute("SELECT 1")
        await cursor.fetchone()
        return True, "ok"
    except Exception:
        logger.warning("[Health] 数据库 readiness 检查失败", exc_info=True)
        return False, "database_unavailable"


def _worker_check(app: FastAPI) -> tuple[bool, str]:
    task_manager = getattr(app.state, "task_manager", None)
    if task_manager is None or not bool(getattr(task_manager, "_is_running", False)):
        return False, "task_manager_not_running"

    for state_name in _WORKER_STATE_NAMES:
        task = getattr(app.state, state_name, None)
        if task is None or task.done() or task.cancelled():
            return False, f"{state_name}_not_running"
    return True, "ok"


async def readiness_details(app: FastAPI) -> dict[str, Any]:
    """返回 readiness 的脱敏检查结果，不包含密钥、prompt 或用户正文。"""
    problems = validate_startup_configuration()
    checks: dict[str, dict[str, str | bool]] = {}

    db_ok, db_message = await _database_check(app)
    checks["database"] = {"ok": db_ok, "status": db_message}

    initialized = bool(getattr(app.state, "initialization_complete", False))
    checks["initialization"] = {
        "ok": initialized,
        "status": "ok" if initialized else "initialization_incomplete",
    }

    ledger = getattr(app.state, "ledger_readiness", None)
    if ledger is not None:
        ledger_report = await ledger.report()
        gate_ok = bool(ledger_report.get("ready_for_authoritative", False))
        checks["ledger_authoritative_gate"] = {
            "ok": gate_ok or not config_manager.get_bool("ledger.authoritative", False),
            "status": "ok" if gate_ok else "blocked_until_migration_and_reconciliation",
        }

    workers_ok, worker_message = _worker_check(app)
    checks["workers"] = {"ok": workers_ok, "status": worker_message}

    directories = {
        "app_data": get_app_data_dir(),
        "temp": get_temp_root(),
        "workspace": get_workspace_dir(),
    }
    directories_ok = all(_directory_writable(path) for path in directories.values())
    checks["directories"] = {
        "ok": directories_ok,
        "status": "ok" if directories_ok else "directory_not_writable",
    }

    if getattr(app.state, "initialization_error", None):
        problems.append("application_initialization_failed")

    ready = not problems and all(bool(item["ok"]) for item in checks.values())
    return {
        "status": "ready" if ready else "not_ready",
        "environment": runtime_environment(),
        "checks": checks,
        "problems": problems,
    }


def liveness_details() -> dict[str, str]:
    """轻量存活结果；刻意不依赖外部服务、数据库或模型。"""
    return {"status": "alive"}


__all__ = [
    "liveness_details",
    "readiness_details",
    "runtime_environment",
    "validate_startup_configuration",
]