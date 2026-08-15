"""
api/projects.py — 作者创作项目管理路由网关
================================================
对外暴露项目/文档管理及触发 AI 自学习的 RESTful API，
高度复用全局日志、拦截器规范，不对接具体业务逻辑代码。
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from core.task_manager import TaskManager
from core.config_manager import config_manager
from core.path_resolver import get_app_data_dir, safe_join
from models.project import AIParseResult, AuthorProject, ProjectDoc
from services.learning_engine import DocumentLearningEngine
from services.project_manager import ProjectManager
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["Projects"], dependencies=[Depends(verify_token)])
# ==========================================
# 依赖注入桩 (在 main.py 启动时会被统一初始化)
# ==========================================
def get_project_manager(request: Request) -> ProjectManager:
    return request.app.state.project_manager
def get_learning_engine(request: Request) -> DocumentLearningEngine:
    return request.app.state.learning_engine
def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.task_manager
# ==========================================
# API 路由
# ==========================================
@router.post("/projects", response_model=AuthorProject, summary="创建新创作项目")
async def create_project(
    project: AuthorProject, 
    pm: ProjectManager = Depends(get_project_manager)
) -> AuthorProject:
    """初始化并创建一个作者项目，可透传 bind_book_ids 关联书库卡片。"""
    try:
        await pm.create_project(project)
        return project
    except ValueError as e:
        logger.warning("创建项目参数冲突: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("创建项目失败: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误，创建项目失败")
@router.get("/projects", response_model=list[AuthorProject], summary="获取所有项目列表")
async def list_projects(pm: ProjectManager = Depends(get_project_manager)) -> list[AuthorProject]:
    try:
        return await pm.list_projects()
    except Exception as e:
        logger.error("查询项目列表失败: %s", e)
        raise HTTPException(status_code=500, detail="查询项目列表失败")
class ProjectModeUpdate(BaseModel):
    mode: Literal["rapid", "think"]
@router.post("/projects/{project_id}/mode", summary="切换项目的默认大模型算力模式")
async def switch_project_mode(
    project_id: str,
    payload: ProjectModeUpdate,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, str]:
    """调整项目底层 default_compute_mode 为 rapid (本地优先) 或 think (云端优先)。"""
    project = await pm.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    project.default_compute_mode = payload.mode
    try:
        await pm.update_project(project)
        return {"message": f"项目 {project_id} 已成功切换至 {payload.mode} 模式"}
    except Exception as e:
        logger.error("切换模式失败: %s", e)
        raise HTTPException(status_code=500, detail="切换项目模式时发生内部错误")

class BindBookRequest(BaseModel):
    book_id: str

import asyncio

@router.post("/projects/{project_id}/bind-book", summary="为项目绑定书库")
async def bind_book_to_project(
    project_id: str,
    payload: BindBookRequest,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, str]:
    """将指定书籍绑定至项目，后续生成将自动参考该书库内容。"""
    project = await pm.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
        
    # 简单校验 book_id 物理文件是否存在（safe_join 防 book_id 路径遍历探测）
    books_dir = get_app_data_dir().parent / "library" / "books"
    try:
        target_path = safe_join(books_dir, f"{payload.book_id}.txt")
    except ValueError:
        raise HTTPException(status_code=404, detail="指定的书籍不存在于书库中")
    exists = await asyncio.to_thread(target_path.exists)
    if not exists:
        raise HTTPException(status_code=404, detail="指定的书籍不存在于书库中")
        
    if payload.book_id not in project.bind_book_ids:
        project.bind_book_ids.append(payload.book_id)
        try:
            await pm.update_project(project)
        except Exception as e:
            logger.error("绑定书籍失败: %s", e)
            raise HTTPException(status_code=500, detail="绑定书籍失败")
            
    return {"message": f"成功将书籍 {payload.book_id} 绑定至项目 {project_id}"}

@router.post("/projects/{project_id}/unbind-book", summary="解绑项目的书库")
async def unbind_book_from_project(
    project_id: str,
    payload: BindBookRequest,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, str]:
    """将指定书籍从项目解绑，停止参考该书库内容。"""
    project = await pm.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
        
    if payload.book_id in project.bind_book_ids:
        project.bind_book_ids.remove(payload.book_id)
        try:
            await pm.update_project(project)
        except Exception as e:
            logger.error("解绑书籍失败: %s", e)
            raise HTTPException(status_code=500, detail="解绑书籍失败")
            
    return {"message": f"成功将书籍 {payload.book_id} 从项目 {project_id} 解绑"}

@router.post("/projects/{project_id}/docs", response_model=ProjectDoc, summary="录入新文档")
async def add_document(
    project_id: str,
    doc: ProjectDoc,
    pm: ProjectManager = Depends(get_project_manager)
) -> ProjectDoc:
    """上传并归档新章节或大纲文档，文件将被保存在本地 APPDATA 的归档目录下。"""
    if doc.project_id != project_id:
        raise HTTPException(status_code=400, detail="文档所属 project_id 与路由不匹配")
    
    # 鉴权该项目是否存在
    project = await pm.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="绑定的项目不存在")
    try:
        await pm.add_document(doc)
        return doc
    except Exception as e:
        logger.error("录入文档失败: %s", e)
        raise HTTPException(status_code=500, detail="持久化存储文档失败，请检查文件系统权限")

@router.get("/projects/{project_id}/docs", response_model=list[ProjectDoc], summary="获取项目文档列表")
async def list_project_docs(
    project_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> list[ProjectDoc]:
    """获取指定项目下的所有文档大纲"""
    if not await pm.exists(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        return await pm.list_project_documents(project_id)
    except Exception as e:
        logger.error("获取项目文档列表失败: %s", e)
        raise HTTPException(status_code=500, detail="服务器内部错误")

@router.get("/docs/{doc_id}", response_model=ProjectDoc, summary="获取单个文档全文")
async def get_document(
    doc_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> ProjectDoc:
    doc = await pm.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc

class UpdateDocRequest(BaseModel):
    raw_content: str

@router.put("/docs/{doc_id}", summary="保存文档内容")
async def update_document(
    doc_id: str,
    req: UpdateDocRequest,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, str]:
    try:
        await pm.update_document_content(doc_id, req.raw_content)
        return {"message": "文档保存成功"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("文档保存失败: %s", e)
        raise HTTPException(status_code=500, detail="文档保存失败")

# ── 文档生命周期增强：重命名 / 删除 / 版本历史 ─────────────────

class RenameDocRequest(BaseModel):
    doc_name: str

@router.put("/docs/{doc_id}/rename", summary="重命名文档")
async def rename_document(
    doc_id: str,
    req: RenameDocRequest,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    try:
        await pm.rename_document(doc_id, req.doc_name)
        return {"success": True, "message": "文档重命名成功", "error_code": None}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("文档重命名失败: %s", e)
        raise HTTPException(status_code=500, detail="文档重命名失败")

@router.delete("/docs/{doc_id}", summary="删除文档（含版本历史）")
async def delete_document(
    doc_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    try:
        await pm.delete_document(doc_id)
        return {"success": True, "message": "文档已删除", "error_code": None}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("文档删除失败: %s", e)
        raise HTTPException(status_code=500, detail="文档删除失败")

@router.get("/docs/{doc_id}/versions", summary="获取文档版本历史")
async def list_document_versions(
    doc_id: str,
    branch_id: str | None = None,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    try:
        versions = await pm.list_document_versions(doc_id, branch_id)
        return {"success": True, "data": versions, "message": "success", "error_code": None}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("获取版本历史失败: %s", e)
        raise HTTPException(status_code=500, detail="获取版本历史失败")

class RestoreVersionRequest(BaseModel):
    version: int

@router.post("/docs/{doc_id}/versions/{version}/restore", summary="回滚文档到指定版本")
async def restore_document_version(
    doc_id: str,
    version: int,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    try:
        await pm.restore_document_version(doc_id, version)
        return {"success": True, "message": f"文档已回滚至版本 {version}", "error_code": None}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("文档回滚失败: %s", e)
        raise HTTPException(status_code=500, detail="文档回滚失败")

# ── 架构整改 1.1：平行宇宙分支管理 API（feature.branch_version_enable 门控） ──

class CreateBranchRequest(BaseModel):
    name: str
    from_branch_id: str | None = None

def _require_branch_feature() -> None:
    """分支 API 门控：feature.branch_version_enable 关闭时拒绝（新增能力，不破坏旧接口）。"""
    if not config_manager.get_bool("feature.branch_version_enable", False):
        raise HTTPException(
            status_code=400,
            detail="分支版本系统未启用（feature.branch_version_enable=false）",
        )

@router.get("/docs/{doc_id}/branches", summary="获取文档全部平行宇宙分支")
async def list_doc_branches(
    doc_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    _require_branch_feature()
    try:
        doc = await pm.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        vc = pm._get_version_control()
        branches = await vc.list_branches(doc.project_id, doc_id)
        return {
            "success": True,
            "data": [b.model_dump() for b in branches],
            "current_branch_id": doc.current_branch_id or "main",
            "message": "success",
            "error_code": None,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/docs/{doc_id}/branches", summary="创建平行宇宙分支")
async def create_doc_branch(
    doc_id: str,
    req: CreateBranchRequest,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    _require_branch_feature()
    try:
        doc = await pm.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        vc = pm._get_version_control()
        branch = await vc.create_branch(
            doc.project_id, doc_id, req.name,
            from_branch_id=req.from_branch_id or doc.current_branch_id or "main",
        )
        return {"success": True, "data": branch.model_dump(), "message": "分支已创建", "error_code": None}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/docs/{doc_id}/branches/{branch_id}/switch", summary="切换文档当前时间线")
async def switch_doc_branch(
    doc_id: str,
    branch_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    _require_branch_feature()
    try:
        doc = await pm.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        vc = pm._get_version_control()
        await vc.switch_branch(doc.project_id, doc_id, branch_id)
        return {"success": True, "message": f"已切换至分支 {branch_id}", "error_code": None}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# ── 补丁3：分支归档与差异对比 API ────────────────────────────────

@router.post("/docs/{doc_id}/branches/{branch_id}/archive", summary="归档分支（只读保留）")
async def archive_doc_branch(
    doc_id: str,
    branch_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    _require_branch_feature()
    try:
        doc = await pm.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        vc = pm._get_version_control()
        branch = await vc.archive_branch(doc.project_id, doc_id, branch_id)
        return {
            "success": True,
            "data": branch.model_dump(),
            "message": f"分支 {branch_id} 已归档",
            "error_code": None,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/docs/{doc_id}/branches/{branch_id}/unarchive", summary="恢复归档分支")
async def unarchive_doc_branch(
    doc_id: str,
    branch_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    _require_branch_feature()
    try:
        doc = await pm.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        vc = pm._get_version_control()
        branch = await vc.unarchive_branch(doc.project_id, doc_id, branch_id)
        return {
            "success": True,
            "data": branch.model_dump(),
            "message": f"分支 {branch_id} 已恢复",
            "error_code": None,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/docs/{doc_id}/branches/archived", summary="获取文档全部归档分支")
async def list_archived_doc_branches(
    doc_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    _require_branch_feature()
    try:
        doc = await pm.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        vc = pm._get_version_control()
        branches = await vc.list_archived_branches(doc.project_id, doc_id)
        return {
            "success": True,
            "data": [b.model_dump() for b in branches],
            "message": "success",
            "error_code": None,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/docs/{doc_id}/branches/{branch_a}/diff/{branch_b}", summary="分支差异对比")
async def diff_doc_branches(
    doc_id: str,
    branch_a: str,
    branch_b: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> dict[str, Any]:
    _require_branch_feature()
    try:
        doc = await pm.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        vc = pm._get_version_control()
        diff = await vc.diff_branches(doc_id, branch_a, branch_b)
        return {"success": True, "data": diff, "message": "success", "error_code": None}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/docs/{doc_id}/learn", summary="触发文档长时 AI 学习")
async def trigger_document_learning(
    doc_id: str,
    engine: DocumentLearningEngine = Depends(get_learning_engine)
) -> dict[str, str]:
    """后台长任务：对长文档进行深度文本切分、并行 AI 风格分析，最终汇聚结构化结果。返回后台任务ID。"""
    try:
        task_id = await engine.submit_learning_task(doc_id)
        return {"message": "AI 解析任务已受理投递", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("投递自学习任务失败: %s", doc_id)
        raise HTTPException(status_code=500, detail="服务器内部错误")
@router.get("/docs/{doc_id}/parse-result", response_model=AIParseResult, summary="读取文档自学习报告")
async def get_document_parse_result(
    doc_id: str,
    pm: ProjectManager = Depends(get_project_manager)
) -> Any:
    """直接读取并返回落地在本地的 ai_parse.json 分析汇总结果。"""
    doc = await pm.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
        
    if not doc.ai_parse_path:
        raise HTTPException(status_code=400, detail="该文档尚未进行自学习或任务仍未完成")
    path = Path(doc.ai_parse_path)
    exists = await asyncio.to_thread(path.exists)
    if not exists:
        logger.warning("解析路径存在但物理文件丢失: %s", path)
        raise HTTPException(status_code=500, detail="解析结果物理文件读取失败")
    try:
        raw_text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        content = json.loads(raw_text)
        return content
    except Exception as e:
        logger.error("读取解析结果文件失败: %s", e)
        raise HTTPException(status_code=500, detail="解析报告数据损坏")
@router.get("/tasks/{task_id}/status", summary="查询通用长任务状态")
async def get_task_status(
    task_id: str,
) -> Any:
    """
    兼容旧入口：以 307 重定向代理到批次1引擎独立任务接口
    GET /api/v1/tasks/{task_id}（api/task_api.py）。

    说明（P2 修复）：原实现在此处手写 RAW SQL 绕过 TaskManager 封装，
    现已迁移至 task_api.py（内部走 TaskManager.get_task_detail 封装方法）。
    本路由保留不删除，仅做透明代理，保证上层前端不报错；
    新接口返回字段为旧接口超集，完全向后兼容。
    """
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/api/v1/tasks/{task_id}", status_code=307)