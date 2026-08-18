"""受控执行 — 文档草稿工具（V0.4 第一批工具：document.draft_create）。

方向报告「四、第一批工具」含"创建文档草稿"。为此提供文件态 `DraftStore`
（DRAFT 状态，safe_join 防 id 路径穿越）＋ `make_document_draft_handler(store)`
作为 `document.draft_create` 的默认 handler：入参 {project_id, content}，返回
draft_id；只建草稿，不落正式文档、不动 Ledger（fail-closed 与"草稿待审"语义一致）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.path_resolver import get_app_data_dir, safe_join

logger = logging.getLogger(__name__)


@dataclass
class Draft:
    draft_id: str
    project_id: str
    content: str
    status: str = "DRAFT"
    created_at: str = ""


class DraftStore:
    """文件态文档草稿存储（目录：%APPDATA%/No0_AI_V4/data/doc_drafts）。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.dir = base_dir or (get_app_data_dir() / "doc_drafts")
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, draft_id: str) -> Path:
        return safe_join(self.dir, f"{draft_id}.json")

    async def create(self, project_id: str, content: str) -> Draft:
        draft = Draft(
            draft_id=f"draft_{uuid4().hex[:12]}",
            project_id=project_id,
            content=content,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        payload = {
            "draft_id": draft.draft_id,
            "project_id": draft.project_id,
            "content": draft.content,
            "status": draft.status,
            "created_at": draft.created_at,
        }
        await asyncio.to_thread(
            self._path(draft.draft_id).write_text,
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return draft

    async def load(self, draft_id: str) -> Draft | None:
        path = self._path(draft_id)
        if not await asyncio.to_thread(path.exists):
            return None
        try:
            data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            return Draft(**{k: data.get(k) for k in Draft.__dataclass_fields__})
        except Exception:  # noqa: BLE001
            logger.warning("加载文档草稿失败: %s", draft_id)
            return None

    async def delete(self, draft_id: str) -> bool:
        path = self._path(draft_id)
        if await asyncio.to_thread(path.exists):
            await asyncio.to_thread(path.unlink)
            return True
        return False

    async def list_ids(self) -> list[str]:
        if not await asyncio.to_thread(self.dir.is_dir):
            return []
        names = await asyncio.to_thread(lambda: list(self.dir.glob("*.json")))
        return sorted(p.stem for p in names)


def make_document_read_handler(store: DraftStore | None = None) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        store_ref = store or DraftStore()
        draft_id = str(args.get("draft_id", "") or "")
        if not draft_id:
            raise ValueError("document.read 需要 draft_id")
        draft = await store_ref.load(draft_id)
        if draft is None:
            raise ValueError(f"草稿不存在: {draft_id}")
        return {"draft_id": draft.draft_id, "content": draft.content, "status": draft.status}

    return handler


def make_document_draft_handler(store: DraftStore | None = None) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        store_ref = store or DraftStore()
        project_id = str(args.get("project_id", "") or "")
        content = str(args.get("content", "") or "")
        if not project_id or not content:
            raise ValueError("document.draft_create 需要 project_id 与 content")
        draft = await store_ref.create(project_id, content)
        return {"draft_id": draft.draft_id, "status": "DRAFT"}

    return handler


draft_store = DraftStore()


__all__ = ["Draft", "DraftStore", "draft_store", "make_document_draft_handler", "make_document_read_handler"]