"""
services/version_control.py — 文档分支版本系统（架构整改 1.1）
==============================================================
将补丁3的 Branch / DocumentVersion 平行宇宙分支模型落地为
SQLite 持久化服务，替代原纯内存桩，并与 ProjectManager 内部的
旧简易版本历史（docs_versions 目录快照）统一为一套体系：

  - branches           ：文档分支（平行宇宙时间线）表
  - document_versions  ：分支内文档内容快照表
  - 旧简易版本历史     ：启动时自动迁移到默认分支 main 下
    （迁移幂等：marker 文件 + DB 查重双保险，旧目录保留不删除）

对外契约：
  - feature.branch_version_enable=true  → ProjectManager 读写走本服务；
  - feature.branch_version_enable=false → 完全回退旧基线逻辑（本服务仅被动建表）。

db 为空时退化为内存模式（兼容旧代码直接实例化调用的场景）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.version_control import (
    DEFAULT_BRANCH_ID,
    Branch,
    DocumentVersion,
    VersionedProjectDocExtension,
)

logger = logging.getLogger(__name__)

_CREATE_BRANCHES_TABLE = """
CREATE TABLE IF NOT EXISTS branches (
    branch_id        TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    name             TEXT NOT NULL,
    parent_branch_id TEXT,
    is_active        INTEGER NOT NULL DEFAULT 0,
    is_archived      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
)
"""
_CREATE_BRANCHES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_branches_doc
ON branches(project_id, doc_id)
"""

_CREATE_VERSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS document_versions (
    version_id     TEXT PRIMARY KEY,
    branch_id      TEXT NOT NULL,
    doc_id         TEXT NOT NULL,
    project_id     TEXT NOT NULL,
    version_no     INTEGER NOT NULL DEFAULT 0,
    content        TEXT NOT NULL,
    commit_message TEXT NOT NULL,
    timestamp      TEXT NOT NULL
)
"""
_CREATE_VERSIONS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_doc_versions_doc
ON document_versions(doc_id, branch_id, version_no)
"""

# 旧简易版本历史迁移完成标记文件名（位于 docs_versions/<doc_id>/ 下）
_MIGRATION_MARKER = ".branch_migrated"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VersionControlService:
    """分支版本系统持久化服务（db=None 时退化为内存模式）。"""

    def __init__(self, db=None) -> None:
        self.db = db
        # 内存退化模式（db 未注入时），保持旧桩可运行
        self._memory_versions: list[DocumentVersion] = []
        self._memory_branches: dict[tuple[str, str], list[Branch]] = {}

    # ── 初始化与迁移 ──────────────────────────────────────────────

    async def initialize(self) -> None:
        """建表（db 模式下）。"""
        if self.db is None:
            return
        await self.db.conn.execute(_CREATE_BRANCHES_TABLE)
        # 补丁3：旧库 branches 表补 is_archived 列（幂等迁移）
        try:
            await self.db.conn.execute(
                "ALTER TABLE branches ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass  # 列已存在（新表建表语句已含），跳过
        await self.db.conn.execute(_CREATE_BRANCHES_INDEX)
        await self.db.conn.execute(_CREATE_VERSIONS_TABLE)
        await self.db.conn.execute(_CREATE_VERSIONS_INDEX)
        await self.db.conn.commit()
        logger.info("[VersionControl] branches / document_versions 表初始化完成")

    async def migrate_legacy_versions(self, project_manager) -> tuple[int, int]:
        """
        将 ProjectManager 旧简易版本历史（docs_versions 目录快照）
        迁移到默认分支 main 下。幂等：已迁移（marker 或 DB 有记录）跳过。

        Args:
            project_manager: ProjectManager 实例（提供 _versions_dir 与 projects_dir）。
        Returns:
            (迁移的文档数, 迁移的版本数)
        """
        if self.db is None:
            logger.warning("[VersionControl] 无 DB 注入，跳过旧版本历史迁移")
            return 0, 0

        migrated_docs = 0
        migrated_versions = 0
        versions_root = project_manager.projects_dir

        try:
            for doc_versions_dir in versions_root.rglob("docs_versions/*"):
                if not doc_versions_dir.is_dir():
                    continue
                index_path = doc_versions_dir / "index.json"
                marker_path = doc_versions_dir / _MIGRATION_MARKER
                if not index_path.exists():
                    continue
                if marker_path.exists():
                    continue

                doc_id = doc_versions_dir.name
                # 反推 project_id：<projects_dir>/<project_id>/docs_versions/<doc_id>
                project_id = doc_versions_dir.parent.parent.name
                if project_id == "docs_versions":
                    continue  # 目录层级不符，跳过保护

                # DB 查重双保险
                row = await self._fetch_one(
                    "SELECT COUNT(*) FROM document_versions WHERE doc_id=?", (doc_id,)
                )
                if row and int(row[0]) > 0:
                    self._write_marker(marker_path)
                    continue

                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("[VersionControl] 旧版本索引读取失败，跳过 %s: %s", index_path, exc)
                    continue

                # 确保默认分支存在
                await self._ensure_default_branch(project_id, doc_id)

                imported = 0
                for item in index:
                    file_name = item.get("file")
                    if not file_name:
                        continue
                    snapshot = doc_versions_dir / file_name
                    if not snapshot.exists():
                        continue
                    try:
                        content = snapshot.read_text(encoding="utf-8")
                    except OSError as exc:
                        logger.warning("[VersionControl] 旧快照读取失败，跳过 %s: %s", snapshot, exc)
                        continue
                    version = DocumentVersion(
                        branch_id=DEFAULT_BRANCH_ID,
                        doc_id=doc_id,
                        project_id=project_id,
                        content=content,
                        commit_message=f"迁移自旧版本历史 v{item.get('version', imported + 1)}",
                        version_no=int(item.get("version", imported + 1)),
                        timestamp=datetime.fromisoformat(
                            str(item.get("timestamp", _now_iso()))
                        ),
                    )
                    await self._insert_version(version, prune=False)
                    imported += 1

                if imported:
                    self._write_marker(marker_path)
                    migrated_docs += 1
                    migrated_versions += imported
                    logger.info(
                        "[VersionControl] 文档 %s 旧版本历史已迁移 %d 版至默认分支 main",
                        doc_id, imported,
                    )
        except Exception as exc:  # 迁移失败不阻断启动
            logger.error("[VersionControl] 旧版本历史迁移异常（不阻断启动）: %s", exc)

        if migrated_docs:
            logger.info(
                "[VersionControl] 迁移完成：%d 个文档 / %d 个版本", migrated_docs, migrated_versions
            )
        return migrated_docs, migrated_versions

    @staticmethod
    def _write_marker(marker_path: Path) -> None:
        try:
            marker_path.write_text("migrated", encoding="utf-8")
        except OSError:
            pass

    # ── 底层 CRUD（db 模式） ──────────────────────────────────────

    async def _execute(self, query: str, params: tuple = ()) -> None:
        if self.db is None:
            return
        await self.db.execute_write(query, params)

    async def _fetch_all(self, query: str, params: tuple = ()) -> list[tuple]:
        if self.db is None:
            return []
        cursor = await self.db.conn.execute(query, params)
        return await cursor.fetchall()

    async def _fetch_one(self, query: str, params: tuple = ()) -> tuple | None:
        if self.db is None:
            return None
        cursor = await self.db.conn.execute(query, params)
        return await cursor.fetchone()

    async def _ensure_default_branch(self, project_id: str, doc_id: str) -> Branch:
        """确保文档存在默认分支 main（不存在则创建并激活）。"""
        branches = await self.list_branches(project_id, doc_id)
        for b in branches:
            if b.branch_id == DEFAULT_BRANCH_ID:
                return b
        branch = Branch(
            branch_id=DEFAULT_BRANCH_ID,
            name="main",
            project_id=project_id,
            doc_id=doc_id,
            is_active=True,
        )
        await self._insert_branch(branch)
        return branch

    async def _insert_branch(self, branch: Branch) -> None:
        if self.db is None:
            self._memory_branches.setdefault((branch.project_id or "", branch.doc_id or ""), []).append(branch)
            return
        await self._execute(
            """INSERT INTO branches
               (branch_id, project_id, doc_id, name, parent_branch_id, is_active, is_archived, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                branch.branch_id,
                branch.project_id or "",
                branch.doc_id or "",
                branch.name,
                branch.parent_branch_id,
                1 if branch.is_active else 0,
                1 if branch.is_archived else 0,
                branch.created_at or _now_iso(),
            ),
        )

    async def _insert_version(self, version: DocumentVersion, prune: bool = True) -> None:
        if self.db is None:
            self._memory_versions.append(version)
            return
        max_keep = self._max_versions()
        ts = version.timestamp.isoformat() if isinstance(version.timestamp, datetime) else str(version.timestamp)
        await self._execute(
            """INSERT INTO document_versions
               (version_id, branch_id, doc_id, project_id, version_no, content, commit_message, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version.version_id,
                version.branch_id,
                version.doc_id,
                version.project_id or "",
                version.version_no or 0,
                version.content,
                version.commit_message,
                ts,
            ),
        )
        if prune:
            # 每分支保留最近 N 版，淘汰最旧
            rows = await self._fetch_all(
                """SELECT version_id FROM document_versions
                   WHERE doc_id=? AND branch_id=? ORDER BY version_no DESC, timestamp DESC""",
                (version.doc_id, version.branch_id),
            )
            for stale in rows[max_keep:]:
                await self._execute(
                    "DELETE FROM document_versions WHERE version_id=?", (stale[0],)
                )

    @staticmethod
    def _max_versions() -> int:
        try:
            from core.config_manager import config_manager

            return int(config_manager.get_int("project.max_doc_versions", 20))
        except Exception:
            return 20

    def _next_version_no(self, versions: list[dict[str, Any]]) -> int:
        if not versions:
            return 1
        return max(int(v.get("version_no") or v.get("version") or 0) for v in versions) + 1

    # ── 分支操作 ──────────────────────────────────────────────────

    async def create_branch(
        self, project_id: str, doc_id: str, new_branch_name: str, from_branch_id: str | None = None
    ) -> Branch:
        """从当前（或指定）分支创建平行宇宙新分支。"""
        parent_id = from_branch_id or DEFAULT_BRANCH_ID
        if not new_branch_name or not new_branch_name.strip():
            raise ValueError("分支名不能为空")
        new_branch = Branch(
            name=new_branch_name.strip(),
            parent_branch_id=parent_id,
            project_id=project_id,
            doc_id=doc_id,
            is_active=False,
        )
        await self._insert_branch(new_branch)
        logger.info(
            "创建平行宇宙分支: %s (基于 %s, doc=%s)", new_branch_name, parent_id, doc_id
        )
        return new_branch

    async def switch_branch(self, project_id: str, doc_id: str, target_branch_id: str) -> None:
        """切换文档当前时间线（更新 branches.is_active 与 project_docs.current_branch_id）。"""
        branches = await self.list_branches(project_id, doc_id)
        if not any(b.branch_id == target_branch_id for b in branches):
            raise ValueError(f"分支不存在: {target_branch_id}")
        for b in branches:
            b.is_active = (b.branch_id == target_branch_id)
        if self.db is None:
            return
        await self._execute(
            "UPDATE branches SET is_active=0 WHERE project_id=? AND doc_id=?",
            (project_id, doc_id),
        )
        await self._execute(
            "UPDATE branches SET is_active=1 WHERE branch_id=?", (target_branch_id,)
        )
        await self._execute(
            "UPDATE project_docs SET current_branch_id=? WHERE doc_id=?",
            (target_branch_id, doc_id),
        )
        logger.info("切换时间线至: %s (doc=%s)", target_branch_id, doc_id)

    async def list_branches(self, project_id: str, doc_id: str) -> list[Branch]:
        if self.db is None:
            return [b for b in self._memory_branches.get((project_id, doc_id), [])]
        rows = await self._fetch_all(
            """SELECT branch_id, project_id, doc_id, name, parent_branch_id, is_active, is_archived, created_at
               FROM branches WHERE project_id=? AND doc_id=?""",
            (project_id, doc_id),
        )
        return [
            Branch(
                branch_id=r[0], project_id=r[1], doc_id=r[2], name=r[3],
                parent_branch_id=r[4], is_active=bool(r[5]),
                is_archived=bool(r[6]) if len(r) > 6 else False,
                created_at=r[7] if len(r) > 7 else _now_iso(),
            )
            for r in rows
        ]

    async def get_active_branch(self, project_id: str, doc_id: str) -> Branch:
        branches = await self.list_branches(project_id, doc_id)
        for b in branches:
            if b.is_active and not b.is_archived:
                return b
        for b in branches:
            if not b.is_archived:
                return b
        if branches:
            return branches[0]
        return await self._ensure_default_branch(project_id, doc_id)

    # ── 补丁3：分支归档（旧版本归档） ─────────────────────────────

    async def archive_branch(self, project_id: str, doc_id: str, branch_id: str) -> Branch:
        """归档分支：标记 is_archived，分支只读保留（不删除任何版本数据）。"""
        if branch_id == DEFAULT_BRANCH_ID:
            raise ValueError("默认分支 main 不允许归档")
        branches = await self.list_branches(project_id, doc_id)
        target = next((b for b in branches if b.branch_id == branch_id), None)
        if target is None:
            raise ValueError(f"分支不存在: {branch_id}")
        if target.is_archived:
            return target
        target.is_archived = True
        target.is_active = False
        if self.db is None:
            return target
        await self._execute(
            "UPDATE branches SET is_archived=1, is_active=0 WHERE branch_id=?",
            (branch_id,),
        )
        # 归档的是当前活跃分支时，回落到 main
        row = await self._fetch_one(
            "SELECT current_branch_id FROM project_docs WHERE doc_id=?", (doc_id,)
        )
        if row and row[0] == branch_id:
            await self._execute(
                "UPDATE project_docs SET current_branch_id=? WHERE doc_id=?",
                (DEFAULT_BRANCH_ID, doc_id),
            )
        logger.info("分支已归档: %s (doc=%s)", branch_id, doc_id)
        return target

    async def unarchive_branch(self, project_id: str, doc_id: str, branch_id: str) -> Branch:
        """恢复归档分支为可切换状态。"""
        branches = await self.list_branches(project_id, doc_id)
        target = next((b for b in branches if b.branch_id == branch_id), None)
        if target is None:
            raise ValueError(f"分支不存在: {branch_id}")
        target.is_archived = False
        if self.db is None:
            return target
        await self._execute(
            "UPDATE branches SET is_archived=0 WHERE branch_id=?", (branch_id,)
        )
        return target

    async def list_archived_branches(self, project_id: str, doc_id: str) -> list[Branch]:
        branches = await self.list_branches(project_id, doc_id)
        return [b for b in branches if b.is_archived]

    async def archive_old_versions(self, doc_id: str, branch_id: str, keep: int) -> int:
        """旧版本归档：将分支内超出保留数量的旧版本标记为归档（移动 content 到归档表）。
        实现：version_no 小于 (max - keep) 的版本从主表移除，压缩进归档 JSON 文件（不删数据）。
        返回归档版本数。
        """
        if keep <= 0:
            raise ValueError("保留版本数必须大于 0")
        versions = await self.list_versions(doc_id, branch_id)
        if len(versions) <= keep:
            return 0
        archived: list[dict[str, Any]] = []
        to_remove_ids: list[str] = []
        for v in versions[:-keep]:
            try:
                content = await self.get_version_content(doc_id, v["version_id"])
            except ValueError:
                continue
            archived.append({**v, "content": content})
            to_remove_ids.append(v["version_id"])

        if self.db is not None:
            for vid in to_remove_ids:
                await self._execute(
                    "DELETE FROM document_versions WHERE version_id=?", (vid,)
                )
        else:
            self._memory_versions = [
                mv for mv in self._memory_versions if mv.version_id not in set(to_remove_ids)
            ]

        # 归档数据落盘（JSON 文件，安全目录）
        if archived:
            archive_dir = self._archive_dir(doc_id, branch_id)
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_file = archive_dir / f"archived_{_now_iso()[:19].replace(':', '-')}.json"
            try:
                archive_file.write_text(
                    json.dumps(archived, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError as exc:
                logger.warning("[VersionControl] 归档文件写入失败: %s", exc)
        logger.info("分支 %s 旧版本归档完成: %d 版", branch_id, len(archived))
        return len(archived)

    def _archive_dir(self, doc_id: str, branch_id: str) -> Path:
        from core.path_resolver import get_app_data_dir

        return get_app_data_dir() / "version_archive" / doc_id / branch_id

    async def diff_branches(
        self, doc_id: str, branch_a: str, branch_b: str
    ) -> dict[str, Any]:
        """对比两个分支的最新版本内容差异（diff-match-patch 兼容结构）。
        返回 {"branch_a", "branch_b", "content_a", "content_b", "line_diff"}，
        其中 line_diff 为逐行 diff 列表 [("=", line) | ("-", line) | ("+", line), ...]。
        """
        versions_a = await self.list_versions(doc_id, branch_a)
        versions_b = await self.list_versions(doc_id, branch_b)
        content_a = ""
        content_b = ""
        if versions_a:
            content_a = await self.get_version_content(doc_id, versions_a[-1]["version_id"])
        if versions_b:
            content_b = await self.get_version_content(doc_id, versions_b[-1]["version_id"])
        return {
            "branch_a": branch_a,
            "branch_b": branch_b,
            "content_a": content_a,
            "content_b": content_b,
            "line_diff": self._line_diff(content_a, content_b),
        }

    @staticmethod
    def _line_diff(a: str, b: str) -> list[tuple[str, str]]:
        """轻量逐行 LCS diff（O(n*m) 内存优化版，纯标准库，无需 diff-match-patch 后端依赖）。"""
        import difflib

        diff = list(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm=""))
        if not diff:
            return []
        result: list[tuple[str, str]] = []
        for line in diff[2:]:  # 跳过 --- +++ 头
            if not line:
                result.append(("=", ""))
            elif line.startswith("+"):
                result.append(("+", line[1:]))
            elif line.startswith("-"):
                result.append(("-", line[1:]))
            else:
                result.append(("=", line))
        return result

    # ── 版本操作 ──────────────────────────────────────────────────

    async def commit_version(
        self,
        project_id: str,
        doc_id: str,
        content: str,
        message: str = "snapshot",
        branch_id: str | None = None,
    ) -> DocumentVersion:
        """在指定分支（默认当前活跃分支）提交内容快照。"""
        if branch_id is None:
            branch = await self.get_active_branch(project_id, doc_id)
            branch_id = branch.branch_id
        existing = await self.list_versions(doc_id, branch_id)
        version_no = self._next_version_no(existing)
        version = DocumentVersion(
            branch_id=branch_id,
            doc_id=doc_id,
            project_id=project_id,
            content=content,
            commit_message=message or "snapshot",
            version_no=version_no,
        )
        await self._insert_version(version, prune=True)
        logger.info("文档快照保存 [分支:%s]: %s", branch_id, message)
        return version

    async def list_versions(
        self, doc_id: str, branch_id: str | None = None
    ) -> list[dict[str, Any]]:
        """返回版本列表（不含正文，正文按需读取）。"""
        if self.db is None:
            items = [v for v in self._memory_versions if v.doc_id == doc_id]
            if branch_id:
                items = [v for v in items if v.branch_id == branch_id]
            return [
                {
                    "version_id": v.version_id,
                    "version": v.version_no or i + 1,
                    "branch_id": v.branch_id,
                    "timestamp": v.timestamp.isoformat() if isinstance(v.timestamp, datetime) else str(v.timestamp),
                    "size": len(v.content),
                    "commit_message": v.commit_message,
                }
                for i, v in enumerate(items)
            ]

        query = """SELECT version_id, branch_id, version_no, content, commit_message, timestamp
                   FROM document_versions WHERE doc_id=?"""
        params: list[Any] = [doc_id]
        if branch_id:
            query += " AND branch_id=?"
            params.append(branch_id)
        query += " ORDER BY version_no ASC, timestamp ASC"
        rows = await self._fetch_all(query, tuple(params))
        return [
            {
                "version_id": r[0],
                "version": r[2],
                "branch_id": r[1],
                "timestamp": r[5],
                "size": len(r[3]),
                "commit_message": r[4],
            }
            for r in rows
        ]

    async def get_version_content(self, doc_id: str, version_id: str) -> str:
        """读取指定版本的正文。"""
        if self.db is None:
            for v in self._memory_versions:
                if v.doc_id == doc_id and v.version_id == version_id:
                    return v.content
            raise ValueError(f"版本不存在: {version_id}")
        row = await self._fetch_one(
            "SELECT content FROM document_versions WHERE version_id=? AND doc_id=?",
            (version_id, doc_id),
        )
        if row is None:
            raise ValueError(f"版本不存在: {version_id}")
        return row[0]

    # ── 兼容旧内存桩签名（补丁3 原始接口形态） ─────────────────────
    # 旧代码若持有 VersionedProjectDocExtension 调用，自动适配：

    def create_branch_for_doc_ext(
        self, doc_ext: VersionedProjectDocExtension, new_branch_name: str
    ) -> Branch:
        """同步兼容入口：基于扩展对象创建分支（内存视图即时更新）。"""
        new_branch = Branch(
            name=new_branch_name,
            parent_branch_id=doc_ext.current_branch_id,
            is_active=False,
        )
        doc_ext.branches.append(new_branch)
        return new_branch

    def switch_branch_for_doc_ext(
        self, doc_ext: VersionedProjectDocExtension, target_branch_id: str
    ) -> Optional[DocumentVersion]:
        """同步兼容入口：切换扩展对象的时间线。"""
        for branch in doc_ext.branches:
            branch.is_active = (branch.branch_id == target_branch_id)
        doc_ext.current_branch_id = target_branch_id
        branch_versions = [v for v in self._memory_versions if v.branch_id == target_branch_id]
        if branch_versions:
            return sorted(branch_versions, key=lambda x: x.timestamp, reverse=True)[0]
        return None
