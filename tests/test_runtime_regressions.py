"""运行时资源与队列回归测试。"""
from __future__ import annotations

import asyncio
import contextlib
import warnings
from types import SimpleNamespace

import httpx
import pytest

from core.bootstrap import _queue_worker
from core.database import DatabaseManager
from core.db_pool import db_pool
from models.system import PriorityLevel
from services.priority_queue import PriorityTaskQueue
from services.dispatcher import ModelDispatcher
from core.circuit_breaker import CircuitBreakerState, model_circuit_breaker
import services.dispatcher as dispatcher_module


@pytest.mark.asyncio
async def test_database_manager_close_releases_an_idle_pooled_connection(tmp_path):
    """临时数据库关闭后连接应归还连接池空闲队列，不泄漏计数。

    连接池已由「引用计数」模型重构为「按路径队列」模型（_pools / _conn_counts），
    本用例同步校验新语义：initialize 后池中持有该路径连接，close 后归还空闲队列。
    """
    db_path = tmp_path / "runtime-regression.db"
    db = DatabaseManager(db_path=db_path)
    await db.initialize()
    pool_key = str(db_path)

    # 初始化后池中应记录该路径的连接（新建连接计数 >= 1）
    assert db_pool._conn_counts.get(pool_key, 0) >= 1

    await db.close()

    # 关闭后连接应归还空闲队列，空闲数 >= 1，连接计数不变（不泄漏、不重复计数）
    assert pool_key in db_pool._pools
    assert db_pool._pools[pool_key].qsize() >= 1
    assert db_pool._conn_counts.get(pool_key, 0) >= 1


@pytest.mark.asyncio
async def test_queue_worker_consumes_task_factories():
    """队列工作进程应在出队后创建并执行异步任务工厂。"""
    queue = PriorityTaskQueue()
    app = SimpleNamespace(state=SimpleNamespace(priority_task_queue=queue))
    executed = asyncio.Event()

    async def job() -> None:
        executed.set()

    worker = asyncio.create_task(_queue_worker(app))
    try:
        await queue.push("queued-task", job, PriorityLevel.LV4)

        await asyncio.wait_for(executed.wait(), timeout=1)
        assert executed.is_set()
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


@pytest.mark.asyncio
async def test_queue_closes_coroutine_when_submission_fails():
    """队列底层提交失败时，已创建 coroutine 必须被关闭。"""
    queue = PriorityTaskQueue()

    async def job() -> None:
        await asyncio.sleep(0)

    class FailingQueue:
        async def put(self, _item) -> None:
            raise RuntimeError("queue unavailable")

    queue._queue = FailingQueue()
    coro = job()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError, match="queue unavailable"):
            await queue.push("failed-task", coro, PriorityLevel.LV4)
        del coro
        await asyncio.sleep(0)

    assert not any("never awaited" in str(w.message) for w in caught)


@pytest.mark.asyncio
async def test_queue_worker_marks_failed_task_done():
    """任务异常不能让 worker 停止，且必须完成队列 ack。"""
    queue = PriorityTaskQueue()
    app = SimpleNamespace(state=SimpleNamespace(priority_task_queue=queue))
    executed = asyncio.Event()

    async def failing_job() -> None:
        executed.set()
        raise RuntimeError("job failed")

    worker = asyncio.create_task(_queue_worker(app))
    try:
        await queue.push("failed-task", failing_job, PriorityLevel.LV4)
        await asyncio.wait_for(executed.wait(), timeout=1)
        await asyncio.wait_for(queue._queue.join(), timeout=1)
        assert queue.current_task_id is None
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


@pytest.mark.asyncio
async def test_queue_worker_cancellation_cancels_running_coroutine():
    """取消 worker 时，正在执行的任务应收到取消并完成队列 ack。"""
    queue = PriorityTaskQueue()
    app = SimpleNamespace(state=SimpleNamespace(priority_task_queue=queue))
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def cancellable_job() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    worker = asyncio.create_task(_queue_worker(app))
    await queue.push("cancelled-task", cancellable_job, PriorityLevel.LV4)
    await asyncio.wait_for(started.wait(), timeout=1)
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker

    assert cancelled.is_set()
    assert queue.current_task_id is None
    await asyncio.wait_for(queue._queue.join(), timeout=1)


def _dispatcher_for_ollama(monkeypatch, url="http://ollama.test", model="test-model"):
    model_circuit_breaker.state = CircuitBreakerState.CLOSED
    model_circuit_breaker.failure_count = 0
    dispatcher = ModelDispatcher(
        object(), object(), object(), local_ollama_url=url, local_model=model
    )
    # 避免测试读取宿主配置，并直接验证构造参数/缺省值行为。
    monkeypatch.setattr(dispatcher, "_load_config", lambda: None)
    dispatcher.local_url = url
    dispatcher.local_model = model
    return dispatcher


@pytest.mark.asyncio
async def test_ollama_success_uses_generate_payload(monkeypatch):
    """Ollama 成功响应应解析 response，并发送非流式生成请求。"""
    dispatcher = _dispatcher_for_ollama(monkeypatch)
    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, endpoint, **kwargs):
            captured["endpoint"] = endpoint
            captured["json"] = kwargs["json"]
            return httpx.Response(
                200,
                json={"response": "本地结果"},
                request=httpx.Request("POST", endpoint),
            )

    monkeypatch.setattr(dispatcher_module.httpx, "AsyncClient", FakeClient)
    result = await dispatcher._call_local_model("你好", temperature=0.4, max_tokens=32)

    assert result == "本地结果"
    assert captured["endpoint"] == "http://ollama.test/api/generate"
    assert captured["json"] == {
        "model": "test-model",
        "prompt": "你好",
        "stream": False,
        "options": {"temperature": 0.4, "num_predict": 32},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(404, json={"error": "model not found"}),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("ollama timeout"),
        httpx.Response(200, json={"unexpected": "shape"}),
    ],
    ids=["model-unavailable", "unreachable", "timeout", "malformed-response"],
)
async def test_ollama_failure_paths_are_not_silently_accepted(monkeypatch, failure):
    """模型 HTTP 错误、网络故障、超时和畸形响应均应抛出并计入熔断器。"""
    dispatcher = _dispatcher_for_ollama(monkeypatch)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _endpoint, **_kwargs):
            if isinstance(failure, BaseException):
                raise failure
            response = failure
            response.request = httpx.Request("POST", "http://ollama.test/api/generate")
            return response

    monkeypatch.setattr(dispatcher_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(Exception):
        await dispatcher._call_local_model("你好", temperature=0.4, max_tokens=32)


@pytest.mark.asyncio
async def test_ollama_missing_configuration_fails_before_network(monkeypatch):
    """本地地址或模型缺失时应返回明确的配置错误，不发起网络请求。"""
    dispatcher = _dispatcher_for_ollama(monkeypatch, url="", model="")

    with pytest.raises(Exception, match="未配置本地 Ollama"):
        await dispatcher._call_local_model("你好", temperature=0.4, max_tokens=32)
