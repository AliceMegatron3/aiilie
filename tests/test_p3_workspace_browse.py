"""批次3回归:工作区只读浏览(限工作区)+导入上限+项目路径。"""
from __future__ import annotations

import pytest

from api.workspace import _ensure_within_workspace
from core.exceptions import PathTraversalError


def test_workspace_browse_rejects_outside_path(tmp_path, monkeypatch):
    import api.workspace as ws

    monkeypatch.setattr(ws, "get_workspace_dir", lambda: tmp_path)
    # 工作区内目录可浏览
    inside = tmp_path / "sub"
    inside.mkdir()
    (inside / "a.md").write_text("x", encoding="utf-8")
    target = _ensure_within_workspace(str(inside))
    assert target == inside.resolve()

    # 工作区外拒绝
    outside = tmp_path.parent / "outside_should_not_exist"
    with pytest.raises(PathTraversalError):
        _ensure_within_workspace(str(outside))

    # 绝对路径越界拒绝
    with pytest.raises(PathTraversalError):
        _ensure_within_workspace(str(tmp_path.parent))


def test_import_limits_enforced(tmp_path, monkeypatch):
    import services.project_manager as pm_mod
    from core.config_manager import config_manager

    # 造一个含大量文件的源目录,触发数量上限
    src = tmp_path / "src"
    src.mkdir()
    for i in range(6):
        (src / f"f{i}.md").write_text("内容", encoding="utf-8")

    monkeypatch.setattr(
        config_manager, "get_int",
        lambda key, default: 3 if key == "workspace.import_max_files" else default,
    )

    from services.project_manager import ProjectManager

    pm = ProjectManager.__new__(ProjectManager)  # 不跑完整构造,仅测校验逻辑
    # 需要 projects_dir 属性以测 project_workspace_path
    from pathlib import Path

    pm.projects_dir = tmp_path

    # project_workspace_path 落在工作区
    assert pm.project_workspace_path("abc") == tmp_path / "abc"

    import asyncio

    with pytest.raises(ValueError) as exc:
        asyncio.run(pm.import_local_project_folder(str(src)))
    assert "超过上限" in str(exc.value)


def test_workspace_browse_endpoint_mounted():
    from api.api_router import api_router

    paths = {(getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", None) or ()))) for r in api_router.routes}
    flat = {p for p, _ in paths}
    assert any(p.endswith("/workspace/browse") for p in flat)
