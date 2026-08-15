"""
tests/test_database_manager.py — DatabaseManager 单元测试
=========================================================
覆盖：任务 CRUD、分段 CRUD、聊天记录、模型凭证（含脱敏与删除）。
"""
from __future__ import annotations

import pytest

from models.task import CommandTask

pytestmark = pytest.mark.asyncio


async def test_insert_and_get_task(tmp_db):
    await tmp_db.insert_task(CommandTask(task_id="t1", raw_command="写一章小说", priority=2))
    task = await tmp_db.get_task("t1")
    assert task is not None
    assert task["task_id"] == "t1"
    assert task["raw_command"] == "写一章小说"
    assert task["status"] == "PENDING"


async def test_update_task_status_sets_completed_at(tmp_db):
    await tmp_db.insert_task(CommandTask(task_id="t2", raw_command="x"))
    await tmp_db.update_task_status("t2", "COMPLETED")
    task = await tmp_db.get_task("t2")
    assert task["status"] == "COMPLETED"
    assert task["completed_at"] is not None


async def test_update_task_status_records_error(tmp_db):
    await tmp_db.insert_task(CommandTask(task_id="t3", raw_command="x"))
    await tmp_db.update_task_status("t3", "FAILED", error_message="boom")
    task = await tmp_db.get_task("t3")
    assert task["error_message"] == "boom"


async def test_get_active_tasks_filters_terminal(tmp_db):
    await tmp_db.insert_task(CommandTask(task_id="a1", raw_command="x", priority=1))
    await tmp_db.insert_task(CommandTask(task_id="a2", raw_command="y", priority=2))
    await tmp_db.update_task_status("a2", "COMPLETED")
    active = await tmp_db.get_active_tasks()
    assert [t["task_id"] for t in active] == ["a1"]


async def test_segment_crud(tmp_db):
    await tmp_db.insert_task(CommandTask(task_id="s1", raw_command="x"))
    await tmp_db.insert_segment({
        "segment_id": "seg1",
        "parent_task_id": "s1",
        "content_payload": "第一章内容",
        "sequence_order": 0,
        "status": "PENDING",
        "tail_context": {"prev": "无"},
    })
    await tmp_db.update_segment_status(
        "seg1", "COMPLETED", result_content="结果", new_tail={"next": "有"},
    )
    seg = await tmp_db.get_segment("seg1")
    assert seg["status"] == "COMPLETED"
    assert seg["result_content"] == "结果"
    segs = await tmp_db.get_segments_for_task("s1")
    assert len(segs) == 1


async def test_chat_history_roundtrip(tmp_db):
    await tmp_db.insert_chat_history("c1", "sess1", "proj1", "你好", "你好呀")
    rows = await tmp_db.get_chat_history("sess1")
    assert len(rows) == 1
    assert rows[0]["user_query"] == "你好"
    assert rows[0]["ai_result"] == "你好呀"


async def test_model_credentials_masked_and_deleted(tmp_db):
    await tmp_db.insert_model_credential({
        "id": "cred1",
        "name": "主模型",
        "api_endpoint": "https://api.example.com/v1",
        "api_key": "sk-abcdef1234567890",
        "model_tags": ["chat", "fast"],
    })
    creds = await tmp_db.get_all_model_credentials()
    assert len(creds) == 1
    # 对外返回必须脱敏，不泄露明文
    assert "sk-abcdef1234567890" not in creds[0]["api_key"]
    assert creds[0]["model_tags"] == ["chat", "fast"]

    await tmp_db.delete_model_credential("cred1")
    assert await tmp_db.get_all_model_credentials() == []


async def test_missing_task_returns_none(tmp_db):
    assert await tmp_db.get_task("nope") is None
