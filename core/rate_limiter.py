import time
import asyncio
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class TokenBucketRateLimiter(BaseHTTPMiddleware):
    """
    简单内存 Token Bucket 速率限制中间件。
    默认每秒 20 个令牌，最大容量 100，适用于桌面端应用，
    防止前端异常轮询导致后端并发风暴。
    """
    def __init__(self, app, rate: float = 20.0, capacity: int = 100):
        super().__init__(app)
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < 1:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error_code": "RATE_LIMIT_EXCEEDED",
                        "message": "系统负载过高，已触发请求限流。请稍后再试。"
                    }
                )
            self.tokens -= 1
        
        response = await call_next(request)
        return response
