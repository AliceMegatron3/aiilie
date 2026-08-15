import time
import logging
from enum import Enum
from functools import wraps
from typing import Callable, Any, Coroutine

logger = logging.getLogger(__name__)

class CircuitBreakerState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class CircuitBreakerOpenException(Exception):
    """Exception raised when attempting to call a service while the circuit breaker is open."""
    pass

class CircuitBreaker:
    """
    熔断状态机 (Circuit Breaker State Machine)
    - CLOSED: 正常放行请求。如果失败次数达到 threshold，进入 OPEN。
    - OPEN: 拒绝请求。经过 recovery_timeout 后，进入 HALF_OPEN。
    - HALF_OPEN: 允许放行一次测试请求。成功则进入 CLOSED，失败则退回 OPEN。
    """
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    def record_success(self) -> None:
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.info("CircuitBreaker: HALF_OPEN -> CLOSED (Recovery successful)")
            self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.warning("CircuitBreaker: HALF_OPEN -> OPEN (Test request failed)")
            self.state = CircuitBreakerState.OPEN
        elif self.state == CircuitBreakerState.CLOSED and self.failure_count >= self.failure_threshold:
            logger.warning("CircuitBreaker: CLOSED -> OPEN (Threshold exceeded: %d)", self.failure_count)
            self.state = CircuitBreakerState.OPEN

    def check(self) -> None:
        if self.state == CircuitBreakerState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                logger.info("CircuitBreaker: OPEN -> HALF_OPEN (Timeout reached)")
                self.state = CircuitBreakerState.HALF_OPEN
            else:
                raise CircuitBreakerOpenException("Circuit breaker is OPEN.")

def circuit_breaker_decorator(breaker: CircuitBreaker):
    """将熔断器逻辑作为装饰器应用。"""
    def decorator(func: Callable):
        import asyncio
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                breaker.check()
                try:
                    result = await func(*args, **kwargs)
                    breaker.record_success()
                    return result
                except Exception as e:
                    breaker.record_failure()
                    raise e
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                breaker.check()
                try:
                    result = func(*args, **kwargs)
                    breaker.record_success()
                    return result
                except Exception as e:
                    breaker.record_failure()
                    raise e
            return sync_wrapper
    return decorator

# 全局默认模型请求熔断器
model_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
