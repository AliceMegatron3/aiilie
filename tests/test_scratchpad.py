"""Scratchpad 反思草稿区测试（方向报告 P0/P1 #10：服务已修复并纳入主流程）。

覆盖：读写/增量更新/销毁，以及 context_id 路径遍历被 safe_join 拒绝。
"""
from __future__ import annotations

import json
import pytest

from services.scratchpad_manager import ScratchpadManager


def _manager(tmp_path) -> ScratchpadManager:
    manager = ScratchpadManager()
    # 指向隔离的临时草稿目录，避免触碰真实工作区 .ai_index
    manager.scratchpad_dir = tmp_path / "scratchpad"
    manager.scratchpad_dir.mkdir(parents=True, exist_ok=True)
    return manager


@pytest.mark.asyncio
async def test_read_write_update_destroy_roundtrip(tmp_path):
    manager = _manager(tmp_path)
    # 初始为空
    assert await manager.read_scratchpad("book_x") == {}
    # 写入
    await manager.write_scratchpad("book_x", {"a": 1})
    assert await manager.read_scratchpad("book_x") == {"a": 1}
    # 增量更新（标量覆盖 + dict 深合并）
    updated = await manager.update_scratchpad("book_x", {"a": 2, "nested": {"k": "v"}})
    assert updated["a"] == 2
    assert updated["nested"] == {"k": "v"}
    # 销毁
    assert await manager.destroy_scratchpad("book_x") is True
    assert await manager.read_scratchpad("book_x") == {}
    # 销毁不存在的返回 False
    assert await manager.destroy_scratchpad("book_x") is False


@pytest.mark.asyncio
async def test_scratchpad_rejects_path_traversal(tmp_path):
    manager = _manager(tmp_path)
    parent = tmp_path.parent
    escaped = parent / "escaped.json"
    # 写入越界 context_id 必须被 safe_join 拒绝（抛 ValueError），不落盘到越界路径
    with pytest.raises(ValueError):
        await manager.write_scratchpad("../escaped", {"a": 1})
    with pytest.raises(ValueError):
        await manager.read_scratchpad("..\\..\\escaped")
    assert not escaped.exists()
    # 合法标识符不受影响
    await manager.write_scratchpad("book_y", {"ok": 1})
    assert (manager.scratchpad_dir / "book_y.json").exists()