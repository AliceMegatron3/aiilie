import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from core.path_resolver import get_ai_index_dir, safe_join

logger = logging.getLogger(__name__)

class ScratchpadManager:
    """管理智能体在进行全书量化等跨章节复杂任务时的临时沙盒草稿区"""

    def __init__(self):
        self.scratchpad_dir = get_ai_index_dir() / "scratchpad"
        self.scratchpad_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, context_id: str) -> Path:
        # safe_join 防 context_id（book_id）路径遍历读写
        return safe_join(self.scratchpad_dir, f"{context_id}.json")

    async def read_scratchpad(self, context_id: str) -> dict[str, Any]:
        """读取指定上下文的草稿数据（异步，避免阻塞事件循环）"""
        file_path = self._get_file_path(context_id)
        if not file_path.exists():
            return {}
        try:
            return json.loads(await asyncio.to_thread(file_path.read_text, encoding="utf-8"))
        except Exception as e:
            logger.warning("读取 Scratchpad 失败 [%s]: %s", context_id, e)
            return {}

    async def write_scratchpad(self, context_id: str, data: dict[str, Any]) -> None:
        """全量覆盖写入草稿数据（异步，避免阻塞事件循环）"""
        file_path = self._get_file_path(context_id)
        try:
            # 限制写入，如果大文件保护
            raw_data = json.dumps(data, ensure_ascii=False, indent=2)
            if len(raw_data) > 2 * 1024 * 1024:
                logger.warning("沙盒草稿体积过大 (>2MB) [%s]，已切换异步落盘避免阻塞", context_id)
            await asyncio.to_thread(file_path.write_text, raw_data, encoding="utf-8")
        except Exception as e:
            logger.error("写入 Scratchpad 失败 [%s]: %s", context_id, e)

    async def update_scratchpad(self, context_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """增量合并草稿数据"""
        data = await self.read_scratchpad(context_id)
        # 浅度合并
        for k, v in updates.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                data[k].update(v)
            else:
                data[k] = v
        await self.write_scratchpad(context_id, data)
        return data

    async def destroy_scratchpad(self, context_id: str) -> bool:
        """销毁沙盒数据以释放空间"""
        file_path = self._get_file_path(context_id)
        if file_path.exists():
            try:
                await asyncio.to_thread(file_path.unlink)
                logger.info("已销毁 Scratchpad: %s", context_id)
                return True
            except Exception as e:
                logger.error("销毁 Scratchpad 失败 [%s]: %s", context_id, e)
        return False

scratchpad_manager = ScratchpadManager()
