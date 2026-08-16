"""
services/project_manager.py — 作者创作项目管理服务
===================================================
实现 AuthorProject 与 ProjectDoc 的全生命周期管理（CRUD）。
复用批次1的 aiosqlite 异步数据库引擎管理项目元数据，并在 %APPDATA% 下管理物理文件。
注：本项目底层复用批次1的 DatabaseManager (原生 aiosqlite)，未使用额外 ORM 以保证性能与架构一致。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.database import DatabaseManager
from core.path_resolver import get_app_data_dir, safe_join
from models.project import AuthorProject, ProjectDoc

logger = logging.getLogger(__name__)

# ==========================================
# 数据库表结构定义
# ==========================================
_CREATE_PROJECTS_TABLE = """
CREATE TABLE IF NOT EXISTS author_projects (
    project_id           TEXT PRIMARY KEY,
    project_name         TEXT NOT NULL,
    genre                TEXT NOT NULL,
    style_tags           TEXT NOT NULL,
    bind_book_ids        TEXT NOT NULL,
    default_compute_mode TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
)
"""

_CREATE_PROJECT_DOCS_TABLE = """
CREATE TABLE IF NOT EXISTS project_docs (
    doc_id         TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    doc_name       TEXT NOT NULL,
    status         TEXT NOT NULL,
    ai_parse_path  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES author_projects(project_id) ON DELETE CASCADE
)
"""

# 项目 JSON 文本列（补丁1/2 扩展），读取时统一反序列化
_PROJECT_JSON_COLUMNS = {
    "style_tags": list,
    "bind_book_ids": list,
    "timelines": list,
    "character_voice_profiles": dict,
}


def _decode_project_json(row_dict: dict[str, Any]) -> dict[str, Any]:
    """把项目行的 JSON 文本列反序列化为 Python 对象（缺列/损坏时兜底默认值）。"""
    for key, factory in _PROJECT_JSON_COLUMNS.items():
        raw = row_dict.get(key)
        if raw is None:
            row_dict[key] = factory()
            continue
        if isinstance(raw, (list, dict)):
            continue
        try:
            row_dict[key] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            row_dict[key] = factory()
    return row_dict


class ProjectManager:
    """
    项目与文档生命周期管理器。
    负责在 SQLite 中管理元数据，并在本地文件系统中管理原始文档存档。

    架构整改 1.1：文档版本统一走分支版本系统（services/version_control.py）。
    feature.branch_version_enable=true  → 读写走 VersionControlService；
    feature.branch_version_enable=false → 完全回退旧简易版本历史（兼容层）。
    """

    def __init__(self, db: DatabaseManager, version_control: Any | None = None) -> None:
        self.db = db
        # 文档物理存储根目录迁移到显式工作区
        from core.path_resolver import get_workspace_dir
        self.projects_dir = get_workspace_dir()
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        # 架构整改 1.1：分支版本系统（懒加载，避免 import 环）
        self.version_control = version_control

    def _get_version_control(self) -> Any:
        """懒加载分支版本系统服务。"""
        if self.version_control is None:
            from services.version_control import VersionControlService

            self.version_control = VersionControlService(self.db)
        return self.version_control

    def _branch_version_enabled(self) -> bool:
        """feature 开关：关闭时完全回退旧基线逻辑。"""
        from core.config_manager import config_manager

        return config_manager.get_bool("feature.branch_version_enable", False)

    async def initialize(self) -> None:
        """初始化项目管理相关的数据库表。"""
        await self.db.conn.execute(_CREATE_PROJECTS_TABLE)
        await self.db.conn.execute(_CREATE_PROJECT_DOCS_TABLE)
        # 架构整改 1.1：project_docs 增加 current_branch_id 列（旧库无损迁移）
        try:
            await self.db.conn.execute(
                "ALTER TABLE project_docs ADD COLUMN current_branch_id TEXT"
            )
        except Exception:
            # 列已存在（重复启动）——SQLite ALTER 重复加列会抛错，正常跳过
            pass
        # 业务补丁1/2：author_projects 增加 timelines / character_voice_profiles 列
        # （JSON 文本列，旧库无损迁移，迁移逻辑 = ALTER 幂等）
        for _col in ("timelines", "character_voice_profiles"):
            try:
                await self.db.conn.execute(
                    f"ALTER TABLE author_projects ADD COLUMN {_col} TEXT"
                )
            except Exception:
                pass  # 列已存在，跳过
        await self.db.conn.commit()
        logger.info("项目管理数据表 (author_projects, project_docs) 初始化完成")

        # 分支版本系统建表 + 旧版本历史迁移（仅开关开启时迁移，关闭保持旧逻辑不动）
        vc = self._get_version_control()
        await vc.initialize()
        if self._branch_version_enabled():
            try:
                await vc.migrate_legacy_versions(self)
            except Exception as exc:
                logger.warning("[ProjectManager] 旧版本历史迁移异常（不阻断启动）: %s", exc)

    # ==========================================
    # Project 级别操作
    # ==========================================
    
    async def create_project(self, project: AuthorProject) -> None:
        """创建新项目，存入数据库并初始化物理目录。"""
        # 建立物理目录（safe_join 防路径遍历）
        proj_dir = safe_join(self.projects_dir, project.project_id)
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "docs").mkdir(exist_ok=True)
        (proj_dir / "ai_parse").mkdir(exist_ok=True)

        style_tags_json = json.dumps(project.style_tags, ensure_ascii=False)
        bind_book_ids_json = json.dumps(project.bind_book_ids, ensure_ascii=False)
        timelines_json = json.dumps(
            [t.model_dump() for t in project.timelines], ensure_ascii=False
        )
        voice_profiles_json = json.dumps(
            project.character_voice_profiles, ensure_ascii=False
        )
        now = datetime.now(timezone.utc).isoformat()

        try:
            await self.db.conn.execute(
                """INSERT INTO author_projects 
                   (project_id, project_name, genre, style_tags, bind_book_ids, default_compute_mode,
                    timelines, character_voice_profiles, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project.project_id,
                    project.project_name,
                    project.genre,
                    style_tags_json,
                    bind_book_ids_json,
                    project.default_compute_mode,
                    timelines_json,
                    voice_profiles_json,
                    project.created_at,
                    now
                )
            )
            await self.db.conn.commit()
            logger.info("成功创建项目: %s [%s]", project.project_name, project.project_id)
        except aiosqlite.IntegrityError:
            logger.warning("项目ID已存在: %s", project.project_id)
            raise ValueError(f"项目 {project.project_id} 已存在")

    async def get_project(self, project_id: str) -> AuthorProject | None:
        """获取项目详情。"""
        cursor = await self.db.conn.execute(
            "SELECT * FROM author_projects WHERE project_id = ?", (project_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
            
        columns = [desc[0] for desc in cursor.description]
        row_dict = dict(zip(columns, row))
        
        # 反序列化 JSON 字段（含补丁1/2 扩展列）
        _decode_project_json(row_dict)
        
        return AuthorProject(**row_dict)

    async def exists(self, project_id: str) -> bool:
        """检查项目是否存在。"""
        cursor = await self.db.conn.execute(
            "SELECT 1 FROM author_projects WHERE project_id = ?", (project_id,)
        )
        row = await cursor.fetchone()
        return row is not None

    async def list_projects(self) -> list[AuthorProject]:
        """获取所有项目列表。"""
        cursor = await self.db.conn.execute("SELECT * FROM author_projects ORDER BY updated_at DESC")
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        
        results = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            _decode_project_json(row_dict)
            results.append(AuthorProject(**row_dict))
        return results

    async def update_project(self, project: AuthorProject) -> None:
        """更新项目元数据。"""
        style_tags_json = json.dumps(project.style_tags, ensure_ascii=False)
        bind_book_ids_json = json.dumps(project.bind_book_ids, ensure_ascii=False)
        timelines_json = json.dumps(
            [t.model_dump() for t in project.timelines], ensure_ascii=False
        )
        voice_profiles_json = json.dumps(
            project.character_voice_profiles, ensure_ascii=False
        )
        now = datetime.now(timezone.utc).isoformat()
        
        await self.db.conn.execute(
            """UPDATE author_projects SET 
               project_name=?, genre=?, style_tags=?, bind_book_ids=?, default_compute_mode=?,
               timelines=?, character_voice_profiles=?, updated_at=?
               WHERE project_id=?""",
            (
                project.project_name,
                project.genre,
                style_tags_json,
                bind_book_ids_json,
                project.default_compute_mode,
                timelines_json,
                voice_profiles_json,
                now,
                project.project_id
            )
        )
        await self.db.conn.commit()
        logger.debug("已更新项目元数据: %s", project.project_id)

    async def rename_project(self, project_id: str, new_name: str) -> None:
        """重命名子项目 (修改数据库元数据，因为物理目录名是UUID不需要动)"""
        project = await self.get_project(project_id)
        if not project:
            raise ValueError(f"项目不存在: {project_id}")
            
        project.project_name = new_name
        await self.update_project(project)
        logger.info("子项目已重命名为: %s", new_name)

    def project_workspace_path(self, project_id: str) -> Path:
        """返回项目在工作区内的物理目录(批次3,写权限限定工作区)。"""
        from core.path_resolver import safe_join

        return safe_join(self.projects_dir, project_id)

    async def import_local_project_folder(self, folder_path: str) -> AuthorProject:
        """
        本地导入功能：
        输入一个本地绝对路径，将其内容导入并创建为一个子项目。
        递归扫描其中的 txt, md, docx 文件并注册为 ProjectDoc。

        批次3:补充文件数量(≤500)与总大小(≤200MB)上限,对标 library 上传限制。
        """
        import os
        from pathlib import Path
        import uuid
        from core.config_manager import config_manager

        source_dir = Path(folder_path)
        if not source_dir.exists() or not source_dir.is_dir():
            raise ValueError(f"无效的本地文件夹路径: {folder_path}")

        supported_exts = {".txt", ".md", ".docx"}
        max_files = config_manager.get_int("workspace.import_max_files", 500)
        max_bytes = config_manager.get_int("workspace.import_max_mb", 200) * 1024 * 1024

        # 先扫描统计,超限即拒绝(不做一半再失败)
        candidates: list[Path] = []
        total_bytes = 0
        for root, _, files in os.walk(source_dir):
            for file_name in files:
                file_path = Path(root) / file_name
                if file_path.suffix.lower() in supported_exts:
                    candidates.append(file_path)
                    try:
                        total_bytes += file_path.stat().st_size
                    except OSError:
                        pass
        if len(candidates) > max_files:
            raise ValueError(f"导入文件数量 {len(candidates)} 超过上限 {max_files}")
        if total_bytes > max_bytes:
            raise ValueError(
                f"导入总大小 {total_bytes / 1024 / 1024:.1f}MB 超过上限 {max_bytes / 1024 / 1024:.0f}MB"
            )

        # 创建新项目
        project_name = source_dir.name
        project_id = f"proj_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        new_project = AuthorProject(
            project_id=project_id,
            project_name=project_name,
            genre="imported",
            style_tags=["local_import"],
            default_compute_mode="rapid",
            created_at=now,
            updated_at=now
        )

        await self.create_project(new_project)

        for file_path in candidates:
            try:
                # 暂时只做纯文本读取，docx后续可通过 ParserFactory 增强
                if file_path.suffix.lower() in {".txt", ".md"}:
                    content = await asyncio.to_thread(
                        file_path.read_text, encoding="utf-8", errors="ignore"
                    )
                else:
                    content = f"[暂不支持读取 docx 原始内容，需配置 Parser，路径: {file_path}]"

                doc_id = f"doc_{uuid.uuid4().hex[:8]}"
                new_doc = ProjectDoc(
                    doc_id=doc_id,
                    project_id=project_id,
                    doc_name=file_path.stem,
                    raw_content=content,
                    status="DRAFT",
                    created_at=now,
                    updated_at=now
                )
                await self.add_document(new_doc)
            except Exception as e:
                logger.warning("导入本地文件失败: %s - %s", file_path, e)

        logger.info("成功从本地导入子项目: %s, 共导入文件数 %d", project_name, len(candidates))
        return new_project

    # ==========================================
    # Document 级别操作
    # ==========================================

    async def add_document(self, doc: ProjectDoc) -> None:
        """
        添加新文档。
        文档元数据存入 SQLite，原始文本 raw_content 归档存入本地物理文件，防丢失。
        """
        # 1. 物理归档存储 (版本快照基础)
        doc_dir = safe_join(self.projects_dir, doc.project_id, "docs")
        if not doc_dir.exists():
            raise ValueError(f"父项目目录缺失，请先创建项目: {doc.project_id}")
            
        doc_file = safe_join(doc_dir, f"{doc.doc_id}.md")
        await asyncio.to_thread(doc_file.write_text, doc.raw_content, encoding="utf-8")
        
        # 2. 数据库事务记录
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.db.conn.execute(
                """INSERT INTO project_docs 
                   (doc_id, project_id, doc_name, status, ai_parse_path, current_branch_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc.doc_id,
                    doc.project_id,
                    doc.doc_name,
                    doc.status,
                    doc.ai_parse_path,
                    doc.current_branch_id or "main",
                    doc.created_at,
                    now
                )
            )
            await self.db.conn.commit()
            logger.info("已成功添加文档 [%s] 到项目 [%s]", doc.doc_name, doc.project_id)
        except Exception as e:
            # 异常回滚：删除刚才写入的物理文件
            if doc_file.exists():
                doc_file.unlink()
            logger.error("添加文档失败并执行回滚: %s", e)
            raise

    async def get_document(self, doc_id: str) -> ProjectDoc | None:
        """获取文档模型及物理存储的原始文本。"""
        cursor = await self.db.conn.execute(
            "SELECT * FROM project_docs WHERE doc_id = ?", (doc_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
            
        columns = [desc[0] for desc in cursor.description]
        row_dict = dict(zip(columns, row))
        
        # 从本地归档恢复 raw_content
        doc_file = safe_join(self.projects_dir, row_dict["project_id"], "docs", f"{doc_id}.md")
        if doc_file.exists():
            row_dict["raw_content"] = await asyncio.to_thread(doc_file.read_text, encoding="utf-8")
        else:
            row_dict["raw_content"] = ""
            
        return ProjectDoc(**row_dict)

    async def update_document_content(self, doc_id: str, new_content: str) -> None:
        """更新文档的原始文本内容（写入前自动留存版本快照）。

        架构整改 1.1：
        - feature.branch_version_enable=true  → 走分支版本系统（当前活跃分支提交快照）；
        - feature.branch_version_enable=false → 回退旧简易版本历史（兼容层）。
        """
        doc = await self.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")

        doc_dir = safe_join(self.projects_dir, doc.project_id, "docs")
        if not doc_dir.exists():
            doc_dir.mkdir(parents=True, exist_ok=True)

        # 版本历史：写入前将旧内容存档为快照
        if self._branch_version_enabled():
            vc = self._get_version_control()
            branch_id = doc.current_branch_id or "main"
            try:
                await vc.commit_version(
                    project_id=doc.project_id,
                    doc_id=doc.doc_id,
                    content=doc.raw_content or "",
                    message="auto snapshot before update",
                    branch_id=branch_id,
                )
            except Exception as exc:
                logger.warning(
                    "[ProjectManager] 分支版本快照失败（降级继续写入，不阻断）: %s", exc
                )
        else:
            await self._save_version_snapshot(doc, new_content)

        doc_file = safe_join(doc_dir, f"{doc_id}.md")
        await asyncio.to_thread(doc_file.write_text, new_content, encoding="utf-8")

        now = datetime.now(timezone.utc).isoformat()
        await self.db.conn.execute(
            "UPDATE project_docs SET updated_at = ? WHERE doc_id = ?",
            (now, doc_id)
        )
        await self.db.conn.commit()
        logger.info("已更新文档内容 [%s]", doc_id)

    # ── 文档生命周期增强：重命名 / 删除 / 版本历史 ─────────────────

    async def rename_document(self, doc_id: str, new_name: str) -> None:
        """重命名文档（仅改元数据，物理文件名保持 doc_id 不变）。"""
        if not new_name or not new_name.strip():
            raise ValueError("文档名称不能为空")
        doc = await self.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")
        now = datetime.now(timezone.utc).isoformat()
        await self.db.conn.execute(
            "UPDATE project_docs SET doc_name = ?, updated_at = ? WHERE doc_id = ?",
            (new_name.strip(), now, doc_id),
        )
        await self.db.conn.commit()
        logger.info("文档已重命名: %s → %s", doc_id, new_name)

    async def delete_document(self, doc_id: str) -> None:
        """删除文档：元数据先行（DB 为准），物理文件与版本快照清理尽力而为。

        顺序说明：先删 DB 行保证文档立即从所有列表消失；
        物理清理失败仅记录警告，孤儿文件可被后续维护操作回收，不阻断主流程。
        """
        doc = await self.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")

        # 1. 删除元数据行（DB 为唯一事实源，先删保证界面一致性）
        await self.db.conn.execute(
            "DELETE FROM project_docs WHERE doc_id = ?", (doc_id,)
        )
        await self.db.conn.commit()

        # 2. 删除物理正文（尽力而为）
        doc_file = safe_join(self.projects_dir, doc.project_id, "docs", f"{doc_id}.md")
        try:
            if doc_file.exists():
                doc_file.unlink()
        except OSError as e:
            logger.warning("删除文档物理文件失败（元数据已删）: %s - %s", doc_file, e)

        # 3. 删除版本历史目录（尽力而为）
        versions_dir = self._versions_dir(doc.project_id, doc_id)
        try:
            if versions_dir.exists():
                import shutil
                shutil.rmtree(versions_dir, ignore_errors=True)
        except OSError as e:
            logger.warning("删除版本历史目录失败: %s - %s", versions_dir, e)

        logger.info("文档已删除 [%s]", doc_id)

    def _versions_dir(self, project_id: str, doc_id: str) -> Path:
        return safe_join(self.projects_dir, project_id, "docs_versions", doc_id)

    async def _save_version_snapshot(self, doc: ProjectDoc, new_content: str) -> None:
        """将文档旧内容存档为版本快照（最多保留 20 版，超出淘汰最旧）。"""
        from core.config_manager import config_manager

        max_versions = config_manager.get_int("project.max_doc_versions", 20)
        versions_dir = self._versions_dir(doc.project_id, doc.doc_id)
        versions_dir.mkdir(parents=True, exist_ok=True)

        old_content = doc.raw_content or ""
        if old_content == new_content:
            return  # 无变化不产生版本

        index_path = versions_dir / "index.json"
        index: list[dict[str, Any]] = []
        if index_path.exists():
            try:
                index = json.loads(await asyncio.to_thread(index_path.read_text, encoding="utf-8"))
            except Exception:
                index = []

        version_no = len(index) + 1
        timestamp = datetime.now(timezone.utc)
        snapshot_file = versions_dir / f"v{version_no}_{timestamp.strftime('%Y%m%d%H%M%S')}.md"
        await asyncio.to_thread(snapshot_file.write_text, old_content, encoding="utf-8")

        index.append(
            {
                "version": version_no,
                "timestamp": timestamp.isoformat(),
                "size": len(old_content),
                "file": snapshot_file.name,
            }
        )
        # 淘汰最旧版本（保留最近 N 版）
        if len(index) > max_versions:
            removed = index[: len(index) - max_versions]
            index = index[len(index) - max_versions:]
            for item in removed:
                stale_file = versions_dir / item["file"]
                if stale_file.exists():
                    stale_file.unlink()

        await asyncio.to_thread(
            index_path.write_text, json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def list_document_versions(self, doc_id: str, branch_id: str | None = None) -> list[dict[str, Any]]:
        """返回文档版本历史（不含正文，正文按需读取）。

        架构整改 1.1：开关开启走分支版本系统，关闭回退旧简易版本历史。
        """
        doc = await self.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")

        if self._branch_version_enabled():
            vc = self._get_version_control()
            return await vc.list_versions(doc_id, branch_id)

        index_path = self._versions_dir(doc.project_id, doc_id) / "index.json"
        if not index_path.exists():
            return []
        try:
            return json.loads(await asyncio.to_thread(index_path.read_text, encoding="utf-8"))
        except Exception:
            return []

    async def restore_document_version(self, doc_id: str, version: int) -> None:
        """将文档回滚到指定版本（回滚动作本身也会产生新版本快照）。

        架构整改 1.1：开关开启走分支版本系统（按 version_no 回滚），
        关闭回退旧简易版本历史（按 version 序号回滚）。
        """
        doc = await self.get_document(doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")

        if self._branch_version_enabled():
            vc = self._get_version_control()
            versions = await vc.list_versions(doc_id, doc.current_branch_id or "main")
            target = next((v for v in versions if v.get("version") == version), None)
            if target is None:
                raise ValueError(f"版本不存在: {version}")
            restored_content = await vc.get_version_content(doc_id, target["version_id"])
            await self.update_document_content(doc_id, restored_content)
            logger.info("文档 %s 已回滚至版本 %d（分支版本系统）", doc_id, version)
            return

        versions = await self.list_document_versions(doc_id)
        target = next((v for v in versions if v.get("version") == version), None)
        if target is None:
            raise ValueError(f"版本不存在: {version}")

        snapshot_file = self._versions_dir(doc.project_id, doc_id) / target["file"]
        if not snapshot_file.exists():
            raise ValueError(f"版本快照文件缺失: {target['file']}")

        restored_content = await asyncio.to_thread(snapshot_file.read_text, encoding="utf-8")
        await self.update_document_content(doc_id, restored_content)
        logger.info("文档 %s 已回滚至版本 %d", doc_id, version)

    async def list_project_documents(self, project_id: str) -> list[ProjectDoc]:
        """获取某项目下的所有文档（不加载全文以提高性能，前端需要时通过 get_document 懒加载）。"""
        cursor = await self.db.conn.execute(
            "SELECT * FROM project_docs WHERE project_id = ? ORDER BY created_at ASC", 
            (project_id,)
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        
        results = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            row_dict["raw_content"] = ""  # 列表不返回正文
            results.append(ProjectDoc(**row_dict))
        return results

    async def count_project_documents(self, project_id: str) -> int:
        """
        轻量统计项目下的文档数量（B1-09 负载预估用）。
        仅 SQL COUNT，不加载文档正文。
        """
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM project_docs WHERE project_id = ?",
            (project_id,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def count_ai_parse_results(self, project_id: str) -> int:
        """
        轻量统计项目下已完成 AI 解析（ai_parse_path 非空）的文档数量
        （B1-09 负载预估用）。仅 SQL COUNT。
        """
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM project_docs WHERE project_id = ?"
            " AND ai_parse_path IS NOT NULL",
            (project_id,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def update_document_parse_result(self, doc_id: str, ai_parse_path: str) -> None:
        """专用于 AI 自学习引擎完成分析后，更新文档对应的结构化结果路径。"""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.conn.execute(
            "UPDATE project_docs SET ai_parse_path = ?, updated_at = ? WHERE doc_id = ?",
            (ai_parse_path, now, doc_id)
        )
        await self.db.conn.commit()
        logger.info("已更新文档的 AI 解析结果路径: %s", doc_id)
