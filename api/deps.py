"""
api/deps.py — 统一依赖注入（FastAPI Depends + app.state）
==========================================================
替代 api/library.py 中的 GlobalDependencies 全局类属性单例。
所有路由一律通过本模块的依赖函数从 request.app.state 取实例：
    - 生命周期由 core/bootstrap.py 统一装配
    - 测试环境可直接覆写 app.state 完成注入替换
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from core.exceptions import InternalError
from core.security import create_auth_dependency

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.task_manager import TaskManager
    from services.card_registry import CardTypeRegistry
    from services.code_execution import CodeExecutionService
    from services.web_access import WebAccessService
    from services.global_router import GlobalRouter
    from services.indexer import CardIndexer
    from services.ledger_repository import LedgerRepository
    from services.ledger_read_facade import LedgerReadFacade
    from services.ledger_readiness import LedgerReadiness
    from services.skill_governance import SkillGovernance
    from services.load_estimator import LoadEstimator
    from services.project_manager import ProjectManager
    from services.quantifier import BookQuantifier
    from services.reflection_trigger import ReflectionTrigger
    from services.task_manager import TaskManager as Batch1TaskManager


# 鉴权依赖（启动时快照 security.require_auth 配置，全路由复用同一闭包）。
# 各路由模块通过 `router = APIRouter(dependencies=[Depends(verify_token)])`
# 或在函数签名 `Depends(verify_token)` 挂载。
verify_token = create_auth_dependency()


def _state(request: Request, name: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise InternalError(f"服务 {name} 尚未初始化")
    return value


def get_db(request: Request) -> "DatabaseManager":
    return _state(request, "db")


def get_task_manager(request: Request) -> "TaskManager":
    return _state(request, "task_manager")


def get_batch1_task_manager(request: Request) -> "Batch1TaskManager":
    return _state(request, "batch1_task_manager")


def get_indexer(request: Request) -> "CardIndexer":
    return _state(request, "indexer")


def get_ledger_repository(request: Request) -> "LedgerRepository":
    return _state(request, "ledger_repository")


def get_skill_governance(request: Request) -> "SkillGovernance":
    return _state(request, "skill_governance")


def get_ledger_read_facade(request: Request) -> "LedgerReadFacade":
    return _state(request, "ledger_read_facade")


def get_ledger_readiness(request: Request) -> "LedgerReadiness":
    return _state(request, "ledger_readiness")


def get_registry(request: Request) -> "CardTypeRegistry":
    return _state(request, "registry")


def get_quantifier(request: Request) -> "BookQuantifier":
    return _state(request, "quantifier")


def get_code_execution_service(request: Request) -> "CodeExecutionService":
    return _state(request, "code_execution_service")


def get_web_access_service(request: Request) -> "WebAccessService":
    return _state(request, "web_access_service")


def get_project_manager(request: Request) -> "ProjectManager":
    return _state(request, "project_manager")


def get_global_router(request: Request) -> "GlobalRouter":
    return _state(request, "global_router")


def get_model_dispatcher(request: Request) -> "ModelDispatcher":
    return _state(request, "model_dispatcher")


def get_reflection_trigger(request: Request) -> "ReflectionTrigger":
    return _state(request, "reflection_trigger")


def get_load_estimator(request: Request) -> "LoadEstimator":
    return _state(request, "load_estimator")


def get_llm_client(request: Request):
    """全局 LLM 适配器（bootstrap 装配的 app.state.llm_client，未启用云端时为 None）。"""
    return getattr(request.app.state, "llm_client", None)
