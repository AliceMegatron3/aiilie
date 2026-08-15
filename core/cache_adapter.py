import json
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 内存后端最大条目数（超出触发 LRU 淘汰）
_DEFAULT_MAX_SIZE = 1024


class CacheAdapter:
    def __init__(
        self,
        backend: str = "memory",
        redis_url: Optional[str] = None,
        max_size: int = _DEFAULT_MAX_SIZE,
    ):
        self.backend = backend
        # 内存后端：OrderedDict 存 key -> (value, expire_at)，实现 LRU + TTL
        self._memory_store: OrderedDict[str, tuple[Any, Optional[float]]] = OrderedDict()
        self._max_size = max_size
        self._redis = None

        if self.backend == "redis" and redis_url:
            try:
                import redis.asyncio as redis
                self._redis = redis.from_url(redis_url)
                logger.info("已连接至 Redis 缓存层。")
            except ImportError:
                logger.warning("未安装 redis 库，降级为内存缓存。")
                self.backend = "memory"

    async def get(self, key: str) -> Optional[Any]:
        if self.backend == "redis" and self._redis:
            val = await self._redis.get(key)
            return json.loads(val) if val else None

        item = self._memory_store.get(key)
        if item is None:
            return None
        value, expire_at = item
        if expire_at is not None and time.monotonic() >= expire_at:
            # 已过期：删除并返回 miss
            self._memory_store.pop(key, None)
            return None
        # 命中：LRU 移到末尾（最近使用）
        self._memory_store.move_to_end(key)
        return value

    async def set(self, key: str, value: Any, expire_seconds: int = 3600):
        if self.backend == "redis" and self._redis:
            await self._redis.setex(key, expire_seconds, json.dumps(value))
            return

        expire_at = time.monotonic() + expire_seconds if expire_seconds > 0 else None
        self._memory_store[key] = (value, expire_at)
        self._memory_store.move_to_end(key)
        # LRU 淘汰：超出上限逐出最久未访问项
        while len(self._memory_store) > self._max_size:
            self._memory_store.popitem(last=False)

    async def delete(self, key: str):
        if self.backend == "redis" and self._redis:
            await self._redis.delete(key)
        else:
            self._memory_store.pop(key, None)


import os

cache_adapter = CacheAdapter(
    backend=os.getenv("CACHE_BACKEND", "memory"),
    redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
)
