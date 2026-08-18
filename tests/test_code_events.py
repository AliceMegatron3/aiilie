"""WebSocket 任务事件序号/replay 测试。"""
from __future__ import annotations

import pytest

from api.websocket import ConnectionManager


@pytest.mark.asyncio
async def test_task_events_have_monotonic_sequence_without_subscribers():
    manager = ConnectionManager()
    await manager.publish_task_event("code-1", {"phase": "STAGING", "progress": 10})
    await manager.publish_task_event("code-1", {"phase": "EXECUTING", "progress": 50})
    events = manager.task_history("code-1")
    assert [event["sequence"] for event in events] == [1, 2]
    assert manager.task_history("code-1", after_sequence=1)[0]["sequence"] == 2
