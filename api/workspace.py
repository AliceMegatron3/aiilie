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

from pydantic import BaseModel
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
