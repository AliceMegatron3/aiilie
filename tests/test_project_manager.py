"""
tests/test_project_manager.py — ProjectManager 单元测试
========================================================
覆盖：项目创建/查询、文档 CRUD、版本历史（快照/列表/回滚）、
重命名、删除（含元数据删除与物理清理尽力而为）。
"""
from __future__ import annotations

import pytest

from models.project import AuthorProject, ProjectDoc
from services.project_manager import ProjectManager

pytestmark = pytest.mark.asyncio


def _make_project(project_id: str) -> AuthorProject:
    return AuthorProject(
        project_id=project_id,
        project_name="测试项目",
        genre="fantasy",
        style_tags=["热血"],
        bind_book_ids=[],
        default_compute_mode="rapid",
    )


def _make_doc(doc_id: str, project_id: str, content: str = "初始内容") -> ProjectDoc:
    return ProjectDoc(
        doc_id=doc_id,
        project_id=project_id,
        doc_name="测试文档",
        raw_content=content,
        status="DRAFT",
    )


async def test_create_and_get_project(tmp_db, isolated_paths):
    pm = ProjectManager(tmp_db)
    await pm.initialize()
    await pm.create_project(_make_project("proj1"))
    assert await pm.exists("proj1")
    proj = await pm.get_project("proj1")
    assert proj.project_name == "测试项目"


async def test_document_version_history_flow(tmp_db, isolated_paths):
    pm = ProjectManager(tmp_db)
    await pm.initialize()
    await pm.create_project(_make_project("proj_v"))

    await pm.add_document(_make_doc("doc1", "proj_v", content="v1"))
    await pm.update_document_content("doc1", "v2 内容")
    await pm.update_document_content("doc1", "v3 内容")

    versions = await pm.list_document_versions("doc1")
    # 两次变更应产生两个快照（v1 与 v2）
    assert len(versions) == 2
    assert versions[0]["version"] == 1

    # 回滚到 v1：当前内容应恢复，且回滚动作本身生成新版本
    await pm.restore_document_version("doc1", 1)
    doc = await pm.get_document("doc1")
    assert doc.raw_content == "v1"
    assert len(await pm.list_document_versions("doc1")) == 3


async def test_rename_document(tmp_db, isolated_paths):
    pm = ProjectManager(tmp_db)
    await pm.initialize()
    await pm.create_project(_make_project("proj_r"))
    await pm.add_document(_make_doc("doc2", "proj_r"))
    await pm.rename_document("doc2", "新名字")
    doc = await pm.get_document("doc2")
    assert doc.doc_name == "新名字"


async def test_delete_document_removes_metadata(tmp_db, isolated_paths):
    pm = ProjectManager(tmp_db)
    await pm.initialize()
    await pm.create_project(_make_project("proj_d"))
    await pm.add_document(_make_doc("doc3", "proj_d"))

    await pm.delete_document("doc3")
    assert await pm.get_document("doc3") is None

    docs = await pm.list_project_documents("proj_d")
    assert docs == []


async def test_delete_missing_document_raises(tmp_db, isolated_paths):
    pm = ProjectManager(tmp_db)
    await pm.initialize()
    with pytest.raises(ValueError):
        await pm.delete_document("ghost")
