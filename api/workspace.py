"""
api/workspace.py — 工作区与导入路由
========================================
负责暴露工作区基础信息、子项目本地导入等网关接口。

安全加固（本批次）：/import 的 folder_path 必须位于工作区根目录内，
防止任意路径遍历读取系统任意目录。
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_project_manager, verify_token
from core.exceptions import PathTraversalError
from core.path_resolver import get_workspace_dir, get_ai_index_dir
from core.response import ok
from services.project_manager import ProjectManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["Workspace"], dependencies=[Depends(verify_token)])


def _ensure_within_workspace(folder_path: str) -> Path:
    """校验目标路径必须位于工作区根目录内（防路径遍历/任意目录读取）。"""
    workspace_root = get_workspace_dir().resolve()
    try:
        target = Path(folder_path).resolve()
    except OSError as e:
        raise PathTraversalError(f"无法解析路径: {e}") from e

    if workspace_root not in target.parents and target != workspace_root:
        logger.warning(
            "[Security] 拒绝越界导入: %s 不在工作区 %s 内", target, workspace_root
        )
        raise PathTraversalError(
            f"导入路径必须位于工作区根目录内: {workspace_root}"
        )
    return target


@router.get("/info", summary="获取当前工作区状态")
async def get_workspace_info() -> dict[str, Any]:
    """返回当前系统锁定的 Workspace 大文件夹路径及 AI 挂载点"""
    return ok(
        {
            "workspace_dir": str(get_workspace_dir()),
            "ai_index_dir": str(get_ai_index_dir()),
        }
    )


class ImportRequest(BaseModel):
    folder_path: str


class BrowseRequest(BaseModel):
    path: str = Field(default="", description="工作区内的相对或绝对路径,空=工作区根")


@router.post("/browse", summary="浏览工作区目录(只读,限工作区内)")
async def browse_workspace(req: BrowseRequest) -> dict[str, Any]:
    """返回目录下的子目录与文件清单(名称/类型/大小/修改时间)。

    批次3:只读浏览,写权限仍限定在工作区内;越界路径拒绝。
    """
    import os
    import stat as stat_mod

    workspace_root = get_workspace_dir().resolve()
    # 空路径或相对路径都拼到工作区根,再做越界校验
    raw = (req.path or "").strip()
    base = Path(raw) if raw else workspace_root
    if not base.is_absolute():
        base = workspace_root / raw
    try:
        target = _ensure_within_workspace(str(base))
    except PathTraversalError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not target.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="目标不是目录")

    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            st = child.stat()
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "size": st.st_size if not child.is_dir() else None,
            "modified": st.st_mtime,
        })
    return ok({
        "path": str(target),
        "relative": str(target.relative_to(workspace_root)) if target != workspace_root else "",
        "entries": entries,
    })


@router.post("/import", summary="导入本地文件夹为子项目")
async def import_local_workspace(
    req: ImportRequest,
    pm: ProjectManager = Depends(get_project_manager),
) -> dict[str, Any]:
    """触发本地项目文件夹导入逻辑（路径必须位于工作区根目录内）。"""
    _ensure_within_workspace(req.folder_path)
    try:
        new_project = await pm.import_local_project_folder(req.folder_path)
        return ok(
            {"project_id": new_project.project_id},
            message=f"成功导入子项目：{new_project.project_name}",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("本地导入失败: %s", e)
        raise HTTPException(status_code=500, detail="本地导入失败，请检查路径权限。")
