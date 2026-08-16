"""批次1回归:空产出告警 + 任务结果兜底查询。"""
from __future__ import annotations

import asyncio

import pytest

from core.database import DatabaseManager


@pytest.mark.asyncio
async def test_chat_history_upsert_by_task(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "b1.db")
    await db.initialize()
    try:
        await db.insert_chat_history(
            task_id="t1", session_id="s1", project_id="p1",
            user_query="写一段", ai_result="产出内容",
        )
        rec = await db.get_chat_history_by_task("t1")
        assert rec is not None and rec["ai_result"] == "产出内容"
        assert rec["session_id"] == "s1"
        # 不存在 → None
        assert await db.get_chat_history_by_task("no-such") is None
    finally:
        await db.close()


def test_empty_output_warning_code_present():
    from pathlib import Path

    src = Path("services/global_router.py").read_text(encoding="utf-8")
    assert "创作产出为空" in src, "空产出告警应存在"
    assert "not (result or \"\").strip()" in src, "空判定应基于去空白后的结果"


def test_result_fallback_endpoint_mounted():
    from api.api_router import api_router

    paths = {r.path for r in api_router.routes}
    assert any("command" in p and "result" in p for p in paths), "兜底查询端点应挂载"
