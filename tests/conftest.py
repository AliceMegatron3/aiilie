"""
tests/conftest.py — 共享测试夹具
=================================
- tmp_db: 使用临时目录的 DatabaseManager（避免污染真实 %APPDATA% 数据库）
- isolated_paths: 将 path_resolver 的用户数据/工作区路径重定向到临时目录
- auto_isolated_env: 【0-2 修复】autouse 强制隔离环境变量，确保所有测试默认
  运行在临时 APPDATA/USERPROFILE 下，从根上杜绝测试触碰真实用户数据。
"""
from __future__ import annotations

import pytest

from core.database import DatabaseManager


@pytest.fixture(autouse=True)
def auto_isolated_env(monkeypatch, tmp_path):
    """【0-2 修复】每个测试自动把 APPDATA/USERPROFILE 重定向到临时目录。

    autouse=True 使所有测试无感获得隔离环境，无需显式请求夹具即可避免
    写入真实 %APPDATA%/No0_AI_V4 与 Documents/No0_AI_Workspace。
    """
    appdata = tmp_path / "appdata"
    userprofile = tmp_path / "userprofile"
    appdata.mkdir(parents=True, exist_ok=True)
    userprofile.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("USERPROFILE", str(userprofile))

    # 阶段A fail-closed 总开关已加入代码/插件执行入口。单元测试要验证执行引擎
    # 本身的行为（compileall / 磁盘配额 / 输出上限等），故在测试环境显式开启，
    # 使 execute()/JsonPluginRunner.run() 的纵深防御门控放行；外部 /run API 门控
    # 由各自的集成/回归测试单独覆盖。
    monkeypatch.setenv("AIILIE_CODE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("AIILIE_PLUGINS_EXECUTION_ENABLED", "true")
    # config_manager 在进程内一次性加载环境覆盖，需 reload 使上述开关生效。
    from core.config_manager import config_manager

    config_manager.reload()
    return tmp_path


@pytest.fixture
async def tmp_db(tmp_path):
    """独立临时数据库实例（每个测试独享，互不干扰）。"""
    db = DatabaseManager(db_path=tmp_path / "test_tasks.db")
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """将 app_data / workspace / db 路径重定向到临时目录（隔离真实用户数据）。"""
    import core.path_resolver as pr

    monkeypatch.setattr(pr, "get_app_data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(pr, "get_workspace_dir", lambda: tmp_path / "workspace")
    monkeypatch.setattr(pr, "get_ai_index_dir", lambda: tmp_path / "workspace" / ".ai_index")
    monkeypatch.setattr(pr, "get_db_path", lambda name="tasks.db": tmp_path / "appdata" / name)
    (tmp_path / "appdata").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    return tmp_path


def flatten_api_router(router) -> list:
    """把 FastAPI/Starlette(>=1.6) 的 APIRouter 递归展开为叶子路由对象。

    新版 Starlette 的 api_router.routes 首层可能是 `_IncludedRouter`（无 .path/.methods），
    需经 original_router.routes 递归展开后才能像旧版一样枚举端点路径。
    """
    leaves: list = []

    def walk(routes_list) -> None:
        for r in routes_list:
            if hasattr(r, "path"):
                leaves.append(r)
                continue
            origin = getattr(r, "original_router", None)
            inner = getattr(origin, "routes", None) if origin is not None else None
            if not inner:
                inner = getattr(r, "routes", None)
            if inner:
                walk(inner)

    walk(list(getattr(router, "routes", [])))
    return leaves
